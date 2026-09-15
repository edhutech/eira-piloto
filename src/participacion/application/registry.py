from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from ..core.models import ProgramRecord, ProviderRef, SessionRecord

DEFAULT_PROGRAMS_PATH = Path(os.environ.get(
    "PARTICIPACION_CONFIG_DIR", Path.home() / ".config" / "participacion"
)) / "programs.json"


def _session(value: Mapping[str, Any]) -> SessionRecord:
    return SessionRecord(
        session_number=int(value["session_number"]),
        session_name=str(value["session_name"]),
        source_ref=str(value.get("source_ref", value.get("folder_id", ""))),
    )


def _program(value: Mapping[str, Any]) -> ProgramRecord:
    sessions = tuple(_session(item) for item in value.get("sessions", []))
    program_id = str(value.get("program_id", value.get("folder_id", "")))
    if "source" in value:
        source = ProviderRef(str(value["source"]["provider"]), str(value["source"]["ref"]), value["source"].get("metadata"))
        output = ProviderRef(str(value["output"]["provider"]), str(value["output"]["ref"]), value["output"].get("metadata"))
    else:
        source = ProviderRef("google_drive", str(value["folder_id"]), {"url": str(value.get("folder_url", ""))})
        output = ProviderRef("google_sheets", str(value["sheet_id"]))
    return ProgramRecord(
        program_id=program_id,
        program_name=str(value["program_name"]),
        session_count=int(value["session_count"]),
        participant_mode=str(value["participant_mode"]),
        source=source,
        output=output,
        sessions=sessions,
    )


def load_programs(path: Path = DEFAULT_PROGRAMS_PATH) -> dict[str, ProgramRecord]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("programs.json debe contener un objeto JSON")
    return {str(program_id): _program(value) for program_id, value in raw.items()}


def select_programs(programs: dict[str, ProgramRecord], program_id: str | None = None) -> list[ProgramRecord]:
    if program_id is None:
        return list(programs.values())
    if program_id not in programs:
        raise KeyError(f"Programa no registrado: {program_id}")
    return [programs[program_id]]
