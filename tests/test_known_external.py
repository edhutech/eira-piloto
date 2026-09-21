import csv
import tempfile
import unittest
from pathlib import Path

from participacion.application.known_external import load_known_external
from participacion.application.registry import load_programs, save_programs
from participacion.core.participants import strict_name_key


class KnownExternalConfigurationTests(unittest.TestCase):
    def write(self, rows):
        handle = tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", delete=False)
        with handle:
            writer = csv.DictWriter(handle, fieldnames=("observed_name", "reason", "provenance"))
            writer.writeheader()
            writer.writerows(rows)
        return Path(handle.name)

    def test_loads_strictly_normalized_external_mapping(self):
        path = self.write([{"observed_name": " Externo  Uno ", "reason": "outside", "provenance": "source"}])
        try:
            mapping = load_known_external(path)
            self.assertEqual(mapping[strict_name_key("externo uno")].observed_name, "Externo  Uno")
        finally:
            path.unlink()

    def test_missing_file_fails_closed(self):
        with self.assertRaises(ValueError):
            load_known_external("/tmp/does-not-exist-known-external.csv")

    def test_empty_name_fails_closed(self):
        path = self.write([{"observed_name": "", "reason": "outside", "provenance": "source"}])
        try:
            with self.assertRaises(ValueError):
                load_known_external(path)
        finally:
            path.unlink()

    def test_conflicting_normalized_keys_fail_closed(self):
        path = self.write([
            {"observed_name": "Externo", "reason": "one", "provenance": "source"},
            {"observed_name": " externo ", "reason": "two", "provenance": "source"},
        ])
        try:
            with self.assertRaises(ValueError):
                load_known_external(path)
        finally:
            path.unlink()

    def test_registry_optional_path_round_trip(self):
        raw = {
            "p": {
                "program_id": "p", "program_name": "P", "session_count": 1,
                "participant_mode": "official", "known_external_path": "local.csv",
                "source": {"provider": "google_drive", "ref": "root"},
                "output": {"provider": "google_sheets", "ref": "sheet"},
                "sessions": [{"session_number": 1, "session_name": "S", "source_ref": "folder"}],
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "programs.json"
            save_programs(path, raw)
            loaded = load_programs(path)
            self.assertEqual(loaded["p"].known_external_path, "local.csv")


if __name__ == "__main__":
    unittest.main()
