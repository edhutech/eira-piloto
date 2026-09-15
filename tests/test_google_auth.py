import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from participacion.adapters.google import auth


class GoogleAuthTests(unittest.TestCase):
    def test_paths_use_explicit_and_environment_values(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "token.json"
            secret = Path(directory) / "client.json"
            with patch.dict(os.environ, {
                "PARTICIPACION_GOOGLE_TOKEN": str(token),
                "PARTICIPACION_GOOGLE_CLIENT_SECRET": str(secret),
            }, clear=False):
                self.assertEqual(auth.token_path(), token)
                self.assertEqual(auth.client_secret_path(), secret)

    def test_missing_client_secret_is_clear(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "client secret"):
                auth.client_secret_path()

    def test_authorize_writes_token_without_real_oauth(self):
        class Credentials:
            def to_json(self):
                return json.dumps({"refresh_token": "REDACTED"})
        class Flow:
            @classmethod
            def from_client_secrets_file(cls, path, scopes):
                return cls()
            def run_local_server(self, port):
                return Credentials()
        flow_module = types.ModuleType("google_auth_oauthlib.flow")
        flow_module.InstalledAppFlow = Flow
        package = types.ModuleType("google_auth_oauthlib")
        package.flow = flow_module
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "client.json"
            secret.write_text("{}", encoding="utf-8")
            destination = Path(directory) / "nested" / "token.json"
            with patch.dict(sys.modules, {
                "google_auth_oauthlib": package,
                "google_auth_oauthlib.flow": flow_module,
            }):
                self.assertEqual(auth.authorize(secret, destination), destination)
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8"))["refresh_token"], "REDACTED")
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
