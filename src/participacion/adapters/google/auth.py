from __future__ import annotations

import os
from pathlib import Path
from typing import Any

SCOPES = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents.readonly",
)
DEFAULT_TOKEN_PATH = Path.home() / ".config" / "participacion" / "google_token.json"


def token_path(value: str | Path | None = None) -> Path:
    return Path(value or os.environ.get("PARTICIPACION_GOOGLE_TOKEN", DEFAULT_TOKEN_PATH)).expanduser()


def client_secret_path(value: str | Path | None = None) -> Path:
    configured = value or os.environ.get("PARTICIPACION_GOOGLE_CLIENT_SECRET")
    if not configured:
        raise RuntimeError("Falta OAuth Desktop client secret (--client-secret o PARTICIPACION_GOOGLE_CLIENT_SECRET)")
    return Path(configured).expanduser()


def authorize(client_secret: str | Path | None = None, token: str | Path | None = None) -> Path:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("Instala las dependencias Google: pip install 'participacion-agent[google]'") from exc
    secret = client_secret_path(client_secret)
    if not secret.is_file():
        raise RuntimeError(f"No existe el client secret OAuth: {secret}")
    destination = token_path(token)
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    credentials = flow.run_local_server(port=0)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(credentials.to_json(), encoding="utf-8")
    try:
        destination.chmod(0o600)
    except OSError:
        pass
    return destination


def load_credentials(token: str | Path | None = None) -> Any:
    try:
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError("Instala las dependencias Google: pip install 'participacion-agent[google]'") from exc
    path = token_path(token)
    if not path.is_file():
        raise RuntimeError(f"No existe el token OAuth: {path}")
    return Credentials.from_authorized_user_file(str(path), list(SCOPES))
