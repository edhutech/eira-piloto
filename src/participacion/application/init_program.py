from __future__ import annotations

import json
import re
from pathlib import Path
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
    source_mode: str = "legacy"
    evidence_sessions: tuple[dict[str, Any], ...] = ()


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
               current_folder_name: str = "",
               evidence_sessions: list[dict[str, Any]] | None = None) -> InitPlan:
    if not program_name.strip():
        raise ValueError("El nombre del programa no puede estar vacío")
    session_count = validate_session_count(session_count)
    if participant_mode not in {"auto", "import", "official"}:
        raise ValueError(f"Modo de participantes inválido: {participant_mode}")
    participants = list(imported_participants) if participant_mode in {"import", "official"} else []
    if evidence_sessions is not None:
        sessions = tuple(_validate_evidence_session(item) for item in evidence_sessions)
        if len(sessions) != session_count:
            raise ValueError("evidence_sources debe coincidir con session_count")
        numbers = {int(item["session_number"]) for item in sessions}
        if numbers != set(range(1, session_count + 1)):
            raise ValueError("session_number debe ser único y consecutivo desde 1")
        return InitPlan(program_name, current_folder_name, folder_id, folder_url,
                        session_count, participant_mode, participants, [],
                        existing_sheet_id, existing_sheet_id is None, "evidence_sources", sessions)
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


def load_evidence_sessions(path: str | Path) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    sessions = raw.get("sessions") if isinstance(raw, dict) else None
    if not isinstance(sessions, list) or not sessions:
        raise ValueError("La configuración evidence_sources requiere sessions")
    return [_validate_evidence_session(item) for item in sessions]


def _validate_evidence_session(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Cada sesión evidence_sources debe ser un objeto")
    session_id = str(value.get("session_id", "")).strip()
    name = str(value.get("session_name", "")).strip()
    sources = value.get("evidence_sources")
    if not session_id or not name or not isinstance(sources, list):
        raise ValueError("Una sesión evidence_sources requiere session_id, session_name y lista de fuentes")
    normalized: list[dict[str, str]] = []
    for source in sources:
        if not isinstance(source, dict) or any(not str(source.get(key, "")).strip()
                                               for key in ("provider", "kind", "ref")):
            raise ValueError("Cada evidence_source requiere provider, kind y ref")
        if source["kind"] not in {"container", "artifact"}:
            raise ValueError("evidence_source contiene kind inválido")
        evidence_type = str(source.get("evidence_type", "")).strip()
        if source["kind"] == "artifact" and evidence_type not in {"transcript", "chat"}:
            raise ValueError("un artifact requiere evidence_type transcript o chat")
        if evidence_type and evidence_type not in {"transcript", "chat"}:
            raise ValueError("evidence_source contiene evidence_type inválido")
        normalized_source = {key: str(source[key]).strip() for key in ("provider", "kind", "ref")}
        if evidence_type:
            normalized_source["evidence_type"] = evidence_type
        normalized.append(normalized_source)
    try:
        session_number = int(value["session_number"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Una sesión requiere session_number entero") from exc
    if session_number < 1:
        raise ValueError("session_number debe ser positivo")
    return {"session_number": session_number, "session_id": session_id,
            "session_name": name, "evidence_sources": normalized,
            "planned": not normalized}
