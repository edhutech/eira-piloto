from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal, Mapping, cast

from ..core.models import EvidenceSourceRef, ProgramRecord, ProviderRef, SessionRecord

DEFAULT_PROGRAMS_PATH = Path(os.environ.get(
    "PARTICIPACION_CONFIG_DIR", Path.home() / ".config" / "participacion"
)) / "programs.json"
REGISTRY_VERSION = 1
SUPPORTED_SOURCE_PROVIDERS = {"google_drive"}
SUPPORTED_OUTPUT_PROVIDERS = {"google_sheets"}
PARTICIPANT_MODES = {"auto", "import", "official"}


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} debe ser un entero positivo")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        raise ValueError(f"{field} debe ser un entero positivo")
    if number < 1:
        raise ValueError(f"{field} debe ser un entero positivo")
    return number


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} requiere un valor no vacío")
    return text


def _provider(value: Any, field: str, supported: set[str]) -> ProviderRef:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} requiere un objeto provider/ref")
    provider = _required_text(value.get("provider"), f"{field}.provider")
    if provider not in supported:
        raise ValueError(f"{field}.provider no soportado: {provider}")
    return ProviderRef(provider, _required_text(value.get("ref"), f"{field}.ref"), value.get("metadata"))


def _session(value: Mapping[str, Any]) -> SessionRecord:
    number = _positive_integer(value.get("session_number"), "session_number")
    source_ref = str(value.get("source_ref", value.get("folder_id", "")) or "").strip()
    has_sources = "evidence_sources" in value
    raw_sources = value.get("evidence_sources", ())
    if raw_sources is None:
        raw_sources = ()
    if has_sources and not isinstance(raw_sources, list):
        raise ValueError("INVALID: evidence_sources debe ser una lista")
    if not has_sources:
        raw_sources = []
    if source_ref and has_sources:
        raise ValueError("INVALID: una sesión no puede declarar source_ref y evidence_sources")
    planned = bool(value.get("planned", False))
    if not source_ref and (not has_sources or not raw_sources) and not planned:
        raise ValueError("INVALID: una sesión requiere fuente o planned=true")
    sources: list[EvidenceSourceRef] = []
    for raw in raw_sources:
        if not isinstance(raw, Mapping):
            raise ValueError("INVALID: cada evidence_source debe ser un objeto")
        provider = _required_text(raw.get("provider"), "evidence_sources.provider")
        kind = _required_text(raw.get("kind"), "evidence_sources.kind")
        ref = _required_text(raw.get("ref"), "evidence_sources.ref")
        evidence_type = raw.get("evidence_type")
        if evidence_type not in {"transcript", "chat"}:
            raise ValueError("INVALID: evidence_sources.evidence_type es obligatorio y debe ser transcript o chat")
        if kind not in {"container", "artifact"}:
            raise ValueError(f"INVALID: kind no soportado: {kind}")
        sources.append(EvidenceSourceRef(provider, cast(Literal["container", "artifact"], kind), ref, evidence_type))
    session_id = str(value.get("session_id", "") or "").strip()
    if raw_sources and not session_id:
        raise ValueError("INVALID: una sesión nueva requiere session_id explícito")
    if planned and not session_id:
        raise ValueError("INVALID: una sesión planned requiere session_id explícito")
    return SessionRecord(number, str(value["session_name"]), source_ref, session_id, tuple(sources), planned)


def _program(value: Mapping[str, Any]) -> ProgramRecord:
    if not isinstance(value, Mapping):
        raise ValueError("Cada registro de programa debe ser un objeto JSON")
    raw_sessions = value.get("sessions", [])
    if not isinstance(raw_sessions, list):
        raise ValueError("sessions debe ser una lista")
    sessions = tuple(sorted((_session(item) for item in raw_sessions), key=lambda item: item.session_number))
    if len({session.session_number for session in sessions}) != len(sessions):
        raise ValueError("session_number debe ser único")
    if {session.session_number for session in sessions} != set(range(1, len(sessions) + 1)):
        raise ValueError("session_number debe ser consecutivo desde 1")
    if any(not (session.source_ref or session.evidence_sources or session.planned) for session in sessions):
        raise ValueError("Cada sesión requiere una fuente de evidencia o planned=true")
    program_id = _required_text(value.get("program_id", value.get("folder_id")), "program_id")
    participant_mode = _required_text(value.get("participant_mode"), "participant_mode")
    if participant_mode not in PARTICIPANT_MODES:
        raise ValueError(f"participant_mode no soportado: {participant_mode}")
    session_count = _positive_integer(value.get("session_count"), "session_count")
    if session_count != len(sessions):
        raise ValueError("session_count debe coincidir con len(sessions)")
    if "source" in value:
        source = _provider(value["source"], "source", SUPPORTED_SOURCE_PROVIDERS)
        output = _provider(value.get("output"), "output", SUPPORTED_OUTPUT_PROVIDERS)
    else:
        source = ProviderRef("google_drive", _required_text(value.get("folder_id"), "source.ref"), {"url": str(value.get("folder_url", ""))})
        output = ProviderRef("google_sheets", _required_text(value.get("sheet_id"), "output.ref"))
    return ProgramRecord(program_id, str(value["program_name"]), session_count,
                         participant_mode, source, output, sessions,
                         str(value.get("known_external_path", "") or "").strip())


def program_from_dict(value: Mapping[str, Any]) -> ProgramRecord:
    """Public parser used by local and cloud registries."""
    return _program(value)


def load_programs(path: Path = DEFAULT_PROGRAMS_PATH) -> dict[str, ProgramRecord]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("programs.json debe contener un objeto JSON")
    if "version" in raw:
        if raw.get("version") != REGISTRY_VERSION:
            raise ValueError(f"Versión de programs.json no soportada: {raw.get('version')}")
        raw = raw.get("programs")
        if not isinstance(raw, dict):
            raise ValueError("programs.json v1 requiere programs")
    programs = {}
    for registry_id, value in raw.items():
        program = program_from_dict(value)
        if str(registry_id).strip() and str(registry_id) != program.program_id:
            raise ValueError(f"La clave del registro no coincide con program_id: {registry_id}")
        programs[program.program_id] = program
    return programs


def _encode_program(program: ProgramRecord | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(program, Mapping):
        return dict(program)
    source: dict[str, Any] = {"provider": program.source.provider, "ref": program.source.ref}
    output: dict[str, Any] = {"provider": program.output.provider, "ref": program.output.ref}
    if program.source.metadata is not None:
        source["metadata"] = dict(program.source.metadata)
    if program.output.metadata is not None:
        output["metadata"] = dict(program.output.metadata)
    encoded_sessions = []
    for session in program.sessions:
        item = {"session_number": session.session_number, "session_name": session.session_name}
        if session.planned:
            item["planned"] = True
        if session.source_ref:
            item["source_ref"] = session.source_ref
        if session.session_id:
            item["session_id"] = session.session_id
        if session.evidence_sources:
            item["evidence_sources"] = [
                {"provider": source.provider, "kind": source.kind, "ref": source.ref,
                 "evidence_type": source.evidence_type}
                for source in session.evidence_sources
            ]
        encoded_sessions.append(item)
    return {"program_id": program.program_id, "program_name": program.program_name,
            "session_count": program.session_count, "participant_mode": program.participant_mode,
            "known_external_path": program.known_external_path,
            "source": source, "output": output,
            "sessions": encoded_sessions}


def program_to_dict(program: ProgramRecord | Mapping[str, Any]) -> dict[str, Any]:
    """Public encoder used by local and cloud registries."""
    return _encode_program(program)


def save_programs(path: Path, programs: Mapping[str, ProgramRecord | Mapping[str, Any]]) -> None:
    payload = {"version": REGISTRY_VERSION,
               "programs": {key: program_to_dict(program) for key, program in programs.items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def select_programs(programs: dict[str, ProgramRecord], program_id: str | None = None) -> list[ProgramRecord]:
    if program_id is None:
        return list(programs.values())
    if program_id not in programs:
        raise KeyError(f"Programa no registrado: {program_id}")
    return [programs[program_id]]
