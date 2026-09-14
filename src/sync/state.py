from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_STATE_PATH = Path(__file__).resolve().parents[2] / ".participation_tracker" / "sync_state.json"


def empty_state() -> dict[str, Any]:
    return {"version": 1, "programs": {}}


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("sync_state.json tiene un formato o versión inválidos")
    value.setdefault("programs", {})
    return value


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def file_fingerprint(metadata: dict[str, Any]) -> str:
    selected = {
        "id": metadata.get("id", ""),
        "modifiedTime": metadata.get("modifiedTime", ""),
        "md5Checksum": metadata.get("md5Checksum", ""),
        "size": str(metadata.get("size", "")),
    }
    encoded = json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def stored_file_fingerprint(state: dict[str, Any], program_id: str, session_id: str, file_id: str) -> str | None:
    return state.get("programs", {}).get(program_id, {}).get("sessions", {}).get(session_id, {}).get("files", {}).get(file_id)
