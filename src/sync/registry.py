from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .models import ProgramRecord, SessionRecord

DEFAULT_PROGRAMS_PATH = Path(__file__).resolve().parents[2] / ".participation_tracker" / "programs.json"


def _session(value: Mapping[str, Any]) -> SessionRecord:
    return SessionRecord(
        session_number=int(value["session_number"]),
        session_name=str(value["session_name"]),
        folder_id=str(value["folder_id"]),
    )


def _program(value: Mapping[str, Any]) -> ProgramRecord:
    sessions = tuple(_session(item) for item in value.get("sessions", []))
    return ProgramRecord(
        program_name=str(value["program_name"]),
        folder_id=str(value["folder_id"]),
        folder_url=str(value["folder_url"]),
        session_count=int(value["session_count"]),
        participant_mode=str(value["participant_mode"]),
        sheet_id=str(value["sheet_id"]),
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
