from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .roster import roster_header_positions


@dataclass
class InitPlan:
    program_name: str
    current_folder_name: str
    folder_id: str
    folder_url: str
    session_count: int
    participant_mode: str
    participants: list[dict[str, str]]
    session_folders: list[tuple[str, str | None, bool]]
    sheet_id: str | None
    create_sheet: bool


def validate_session_count(value: str | int) -> int:
    text = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise ValueError("El número de sesiones debe ser un entero mayor que cero")
    return int(text)


def session_folder_name(number: int) -> str:
    if number < 1:
        raise ValueError("El número de sesión debe ser positivo")
    return f"{number:02d} - Sesión {number}"


def validate_participant_columns(headers: Iterable[Any]) -> dict[str, int]:
    return roster_header_positions(list(headers))


def build_plan(*, program_name: str, folder_id: str, folder_url: str,
               session_count: int, participant_mode: str,
               imported_participants: list[dict[str, str]],
               existing_children: dict[str, str],
               existing_sheet_id: str | None,
               current_folder_name: str = "") -> InitPlan:
    if not program_name.strip():
        raise ValueError("El nombre del programa no puede estar vacío")
    session_count = validate_session_count(session_count)
    if participant_mode not in {"auto", "import", "official"}:
        raise ValueError(f"Modo de participantes inválido: {participant_mode}")
    participants = list(imported_participants) if participant_mode in {"import", "official"} else []
    folders = []
    for number in range(1, session_count + 1):
        name = session_folder_name(number)
        folder_id_value = existing_children.get(name)
        folders.append((name, folder_id_value, folder_id_value is None))
    return InitPlan(
        program_name=program_name,
        current_folder_name=current_folder_name,
        folder_id=folder_id,
        folder_url=folder_url,
        session_count=session_count,
        participant_mode=participant_mode,
        participants=participants,
        session_folders=folders,
        sheet_id=existing_sheet_id,
        create_sheet=existing_sheet_id is None,
    )
