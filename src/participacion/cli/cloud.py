from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from ..adapters.google.bootstrap import (
    SHEET_MIME,
    execute_init,
    extract_folder_id,
    extract_spreadsheet_id,
    get_google_services,
    get_google_services_with_docs,
    list_children,
    validate_drive_folder,
)
from ..adapters.google.sheets.cloud_store import GoogleSheetsJsonStore, GoogleSheetsStateStore
from ..adapters.google.sheets.control import GoogleSheetsControlRepository
from ..adapters.google.sheets.operational import GoogleSheetsOperationalRepository
from ..adapters.google.sheets.schema import CLOUD_JSON_HEADERS, PROGRAM_HEADERS
from ..adapters.google.sheets.participants import GoogleSheetsValuesGateway, ParticipantRepository
from ..adapters.pilot.google_participation_source import (
    ReadOnlyGoogleParticipationSource,
    build_participant_resolver,
)
from ..adapters.tabular.google_sheets_reader import read_google_sheet_table
from ..application.attendance_identity import attendance_identity_policy_from_rows
from ..application.init_program import build_plan
from ..application.known_external import known_external_from_rows
from ..application.pilot.config import PilotConfig
from ..application.pilot.config import validate_session_mapping
from ..application.pilot.runner import PilotRunner, PilotSources, RosterApplicabilityResolver
from ..application.external_data.mapping import MappingStatus, map_table
from ..application.modules.resolution import ParticipantResolverAdapter, ResolutionStatus
from ..application.pilot.operational import build_operational_view
from ..application.registry import program_from_dict, save_programs
from ..application.roster import RosterImporter, RosterRecord, roster_records_from_rows, roster_records_to_participants
from ..core.participants import Participant, ParticipantResolver
from .main import _print_results, _sheet_ids, build_runner
from .pilot import _read_attendance_table


CLOUD_BUNDLE_VERSION = 1
CLOUD_BUNDLE_KEY = "eira_bundle"


def _load_json_arg(value: str) -> dict[str, Any]:
    text = sys.stdin.read() if value == "-" else Path(value).expanduser().read_text(encoding="utf-8")
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("La configuración debe ser un objeto JSON")
    return raw


def _source_id(source: Mapping[str, Any]) -> str:
    source_type = str(source.get("type", "")).strip().casefold()
    if source_type != "google_sheets":
        raise ValueError("El flujo cloud-first requiere source.type=google_sheets")
    value = str(source.get("spreadsheet") or source.get("spreadsheet_id") or source.get("url") or "").strip()
    if not value:
        raise ValueError("La fuente Google Sheets requiere spreadsheet/spreadsheet_id/url")
    return extract_spreadsheet_id(value)


def _source_table(sheets: Any, source: Mapping[str, Any]):
    spreadsheet_id = _source_id(source)
    sheet_name = str(source.get("sheet", "")).strip()
    if not sheet_name:
        raise ValueError("La fuente Google Sheets requiere sheet")
    return read_google_sheet_table(sheets, spreadsheet_id, sheet_name=sheet_name)


def _roster_records(sheets: Any, roster: Mapping[str, Any]) -> tuple[list[RosterRecord], set[str]]:
    source = roster.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("roster.source debe ser un objeto")
    table = _source_table(sheets, source)
    raw_mapping = roster.get("mapping")
    if raw_mapping is None:
        rows = [list(table.columns)] + [
            [row.values.get(column) for column in table.columns] for row in table.rows
        ]
        columns = {
            str(column).strip().casefold().replace(" ", "_") for column in table.columns
        }
        return roster_records_from_rows(rows), columns
    if not isinstance(raw_mapping, Mapping):
        raise ValueError("roster.mapping debe ser un objeto")
    mapping = {
        str(key).strip().casefold().replace(" ", "_"): str(value).strip()
        for key, value in raw_mapping.items()
        if str(value).strip()
    }
    missing = [field for field in ("nombre", "correo") if field not in mapping]
    if missing:
        raise ValueError("roster.mapping requiere: " + ", ".join(missing))
    unknown_columns = sorted({column for column in mapping.values() if column not in table.columns})
    if unknown_columns:
        raise ValueError("roster.mapping referencia columnas inexistentes: " + ", ".join(unknown_columns))
    canonical_order = [
        field for field in (
            "participant_id", "nombre", "correo", "aliases", "role",
            "enrollment_status", "start_session", "end_session"
        )
        if field in mapping
    ]
    rows = [canonical_order] + [
        [row.values.get(mapping[field]) for field in canonical_order]
        for row in table.rows
    ]
    return roster_records_from_rows(rows), set(canonical_order)


def _preserve_unspecified_roster_fields(
    records: list[RosterRecord],
    existing: list[Participant],
    columns: set[str],
) -> list[RosterRecord]:
    by_email = {item.correo.strip().casefold(): item for item in existing if item.correo.strip()}
    result: list[RosterRecord] = []
    for record in records:
        current = by_email.get(record.correo.strip().casefold())
        if current is None:
            result.append(record)
            continue
        aliases = record.aliases if "aliases" in columns else tuple(current.aliases or ())
        enrollment = (record.enrollment_status if "enrollment_status" in columns
                      else current.enrollment_status)
        start = record.start_session if "start_session" in columns else current.start_session
        end = record.end_session if "end_session" in columns else current.end_session
        result.append(replace(
            record,
            aliases=aliases,
            enrollment_status=enrollment,
            start_session=start,
            end_session=end,
        ))
    return result


def _default_pilot(record: Mapping[str, Any]) -> dict[str, Any]:
    sessions = list(record.get("sessions", []))
    session_mapping = {
        str(item["session_number"]): {
            "session_id": str(item.get("session_id") or item.get("source_ref") or item["session_number"]),
            "session_order": int(item["session_number"]),
        }
        for item in sessions
    }
    return {
        "program": {
            "program_id": record["program_id"],
            "program_name": record["program_name"],
            "sheet_id": record["output"]["ref"],
        },
        "participation": {"coverage": "UNKNOWN"},
        "session_mapping": session_mapping,
        "longitudinal": {
            "minimum_observations": 1,
            "recent_window_size": 1,
            "trend_threshold": 0,
        },
        "signals": {
            "version": "pilot.v1",
            "participation_silence_streak": {"enabled": True, "minimum_streak": 2},
        },
        "alerts": {"version": "pilot.v1", "silence_level": "OBSERVAR"},
    }


def _normalize_pilot(raw: Mapping[str, Any] | None, record: Mapping[str, Any]) -> dict[str, Any]:
    pilot = dict(raw or _default_pilot(record))
    pilot["program"] = {
        "program_id": record["program_id"],
        "program_name": record["program_name"],
        "sheet_id": record["output"]["ref"],
    }
    if "participation" not in pilot:
        pilot["participation"] = {"coverage": "UNKNOWN"}
    PilotConfig.from_dict(pilot)
    return pilot


def _candidate_record(folder_id: str, folder_url: str, program_name: str,
                     participant_mode: str, sessions: list[Mapping[str, Any]],
                     sheet_id: str | None) -> dict[str, Any]:
    return {
        "program_id": folder_id,
        "program_name": program_name,
        "session_count": len(sessions),
        "participant_mode": participant_mode,
        "source": {"provider": "google_drive", "ref": folder_id,
                    "metadata": {"url": folder_url}},
        "output": {"provider": "google_sheets", "ref": sheet_id or "pending"},
        "sessions": [dict(item) for item in sessions],
    }


def _validate_processed_migration(existing_bundle: Mapping[str, Any] | None,
                                  new_program: Mapping[str, Any],
                                  state: Mapping[str, Any] | None = None) -> None:
    if not existing_bundle:
        return
    old_program = existing_bundle.get("program")
    if not isinstance(old_program, Mapping):
        return
    old_sessions = {str(item.get("session_id", "")): item
                    for item in old_program.get("sessions", []) if isinstance(item, Mapping)}
    new_sessions = {str(item.get("session_id", "")): item
                    for item in new_program.get("sessions", []) if isinstance(item, Mapping)}
    old_state = (state or {}).get("programs", {})
    old_program_state = old_state.get(str(old_program.get("program_id", "")), {})
    session_state = old_program_state.get("sessions", {}) if isinstance(old_program_state, Mapping) else {}
    for session_id, old_session in old_sessions.items():
        current = session_state.get(session_id, {})
        processed = isinstance(current, Mapping) and current.get("status") == "PROCESSED"
        new_session = new_sessions.get(session_id)
        changed = new_session is None or any(
            old_session.get(field) != new_session.get(field)
            for field in ("session_number", "session_name", "evidence_sources", "source_ref")
        )
        if processed and changed:
            raise ValueError(
                f"No se puede modificar la sesión procesada {session_id}; requiere migración explícita"
            )


def _reconcile_program_view(sheets: Any, spreadsheet_id: str, record: Mapping[str, Any],
                            pilot: Mapping[str, Any]) -> None:
    response = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range="'Programa'!A:ZZ"
    ).execute()
    values = response.get("values", [])
    desired = [
        record.get("program_id", ""), record.get("program_name", ""),
        record.get("session_count", ""), record.get("participant_mode", ""),
        (record.get("source") or {}).get("provider", ""),
        (record.get("source") or {}).get("ref", ""),
        ((record.get("source") or {}).get("metadata") or {}).get("url", ""),
        (record.get("output") or {}).get("provider", ""),
        (record.get("output") or {}).get("ref", ""),
        record.get("created_at", ""),
        str((pilot.get("signals") or {}).get("version", "")),
    ]
    if not values:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range="'Programa'!A1",
            valueInputOption="RAW", body={"values": [PROGRAM_HEADERS, desired]},
        ).execute()
        return
    headers = [str(value).strip() for value in values[0]]
    if headers != PROGRAM_HEADERS:
        raise ValueError("Programa requiere el schema canónico antes de reconciliarse")
    current = list(values[1]) if len(values) > 1 else []
    current.extend([""] * (len(PROGRAM_HEADERS) - len(current)))
    if current[9]:
        desired[9] = current[9]
    updates = [{
        "range": f"'Programa'!{_column(index + 1)}2",
        "values": [[value]],
    } for index, value in enumerate(desired) if current[index] != value]
    if updates:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()


def _column(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _validate_rule_lists(bundle: Mapping[str, Any]) -> None:
    known = bundle.get("known_external", [])
    attendance = bundle.get("attendance_identity", [])
    if not isinstance(known, list) or not isinstance(attendance, list):
        raise ValueError("known_external y attendance_identity deben ser listas")
    known_external_from_rows(known)
    if attendance:
        class _Resolver:
            def resolve(self, external_id: str):
                from ..application.modules.resolution import ResolutionResult, ResolutionStatus
                return ResolutionResult(ResolutionStatus.NOT_FOUND)
        attendance_identity_policy_from_rows(attendance, _Resolver())


def _sheet_titles(sheets: Any, spreadsheet_id: str) -> set[str]:
    response = sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        includeGridData=False,
        fields="sheets(properties(title))",
    ).execute()
    return {
        str(item.get("properties", {}).get("title", "")).strip()
        for item in response.get("sheets", [])
        if str(item.get("properties", {}).get("title", "")).strip()
    }


def _cloud_bundle_for_sheet(
    sheets: Any,
    spreadsheet_id: str,
    *,
    folder_id: str,
) -> Mapping[str, Any] | None:
    """Inspect a workbook without mutating it during discovery."""
    titles = _sheet_titles(sheets, spreadsheet_id)
    if "Configuración" not in titles:
        return None
    response = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range="'Configuración'!A:C",
    ).execute()
    values = response.get("values", [])
    if not values:
        return None
    headers = [str(value).strip() for value in values[0]]
    if headers != CLOUD_JSON_HEADERS:
        return None
    bundle: Any = None
    found_bundle = False
    for row in values[1:]:
        key = str(row[0] if row else "").strip()
        if key != CLOUD_BUNDLE_KEY:
            continue
        found_bundle = True
        raw = str(row[1] if len(row) > 1 else "").strip()
        if not raw:
            raise ValueError("Workbook contiene configuración Eira inválida/corrupta; requiere revisión")
        try:
            bundle = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Workbook contiene configuración Eira inválida/corrupta; requiere revisión") from exc
        break
    if found_bundle and (not isinstance(bundle, Mapping) or bundle.get("version") != CLOUD_BUNDLE_VERSION):
        raise ValueError("Workbook contiene configuración Eira inválida/corrupta; requiere revisión")
    if not isinstance(bundle, Mapping) or bundle.get("version") != CLOUD_BUNDLE_VERSION:
        return None
    program_raw = bundle.get("program")
    if not isinstance(program_raw, Mapping):
        return None
    source = program_raw.get("source")
    output = program_raw.get("output")
    if not isinstance(source, Mapping) or not isinstance(output, Mapping):
        return None
    if str(source.get("ref", "")).strip() != folder_id:
        return None
    if str(output.get("ref", "")).strip() not in {"", spreadsheet_id}:
        return None
    return bundle


def _cloud_json_value(sheets: Any, spreadsheet_id: str, sheet_name: str, key: str) -> Any:
    response = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{sheet_name}'!A:C"
    ).execute()
    values = response.get("values", [])
    if values and [str(value).strip() for value in values[0]] != CLOUD_JSON_HEADERS:
        raise ValueError(f"{sheet_name} requiere encabezados exactos: {', '.join(CLOUD_JSON_HEADERS)}")
    for row in values[1:]:
        if str(row[0] if row else "").strip() != key:
            continue
        raw = str(row[1] if len(row) > 1 else "").strip()
        if not raw:
            raise ValueError(f"{sheet_name}: value_json vacío para {key}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{sheet_name}: JSON inválido para {key}") from exc
    return None


def _validate_program_view_schema(sheets: Any, spreadsheet_id: str) -> None:
    response = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range="'Programa'!A:ZZ"
    ).execute()
    values = response.get("values", [])
    if values and [str(value).strip() for value in values[0]] != PROGRAM_HEADERS:
        raise ValueError("Programa requiere el schema canónico antes de aplicar setup")


def _validate_workbook_capabilities(drive: Any, spreadsheet_id: str) -> None:
    if not hasattr(drive, "files"):
        return
    response = drive.files().get(
        fileId=spreadsheet_id,
        fields="capabilities(canEdit,canAddChildren)",
        supportsAllDrives=True,
    ).execute()
    capabilities = response.get("capabilities", {})
    if not isinstance(capabilities, Mapping) or not capabilities.get("canEdit", False):
        raise ValueError("El workbook existente es de solo lectura; setup requiere permisos de edición")


def _validate_legacy_processed_sessions(sheets: Any, spreadsheet_id: str,
                                        new_sessions: list[Mapping[str, Any]]) -> None:
    response = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range="'Control'!A:ZZ"
    ).execute()
    values = response.get("values", [])
    if not values:
        return
    headers = [str(value).strip() for value in values[0]]
    required = {"session_number", "session_name", "folder_id", "processing_status"}
    if not required.issubset(headers):
        raise ValueError("Control requiere columnas canónicas para validar migraciones")
    positions = {header: headers.index(header) for header in required}
    by_number = {int(item.get("session_number", 0)): item for item in new_sessions}
    for row in values[1:]:
        padded = list(row) + [""] * (len(headers) - len(row))
        if str(padded[positions["processing_status"]]).strip() != "PROCESSED":
            continue
        number = int(str(padded[positions["session_number"]]).strip())
        current = by_number.get(number)
        if current is None or str(current.get("session_name", "")) != str(padded[positions["session_name"]]):
            raise ValueError(f"No se puede modificar la sesión procesada {number}; requiere migración explícita")
        source = str(padded[positions["folder_id"]]).strip()
        canonical_session_id = str(current.get("session_id", "") or "").strip()
        new_source = canonical_session_id or str(current.get("source_ref", "")).strip()
        if source and new_source and source != new_source:
            raise ValueError(f"No se puede modificar la fuente de la sesión procesada {number}")


def _find_sheet_in_folder(
    drive: Any,
    sheets: Any,
    folder_id: str,
    sheet_value: str = "",
    *,
    program_name: str = "",
    allow_legacy: bool = False,
) -> str | None:
    candidates = [
        item for item in list_children(drive, folder_id)
        if item.get("mimeType") == SHEET_MIME
    ]
    if sheet_value.strip():
        explicit_id = extract_spreadsheet_id(sheet_value)
        if explicit_id not in {str(item.get("id", "")).strip() for item in candidates}:
            raise ValueError("El workbook indicado por --sheet no pertenece a la carpeta Eira")
        return explicit_id
    cloud_matches: list[tuple[str, Mapping[str, Any]]] = []
    for item in candidates:
        sheet_id = str(item.get("id", "")).strip()
        if not sheet_id:
            continue
        bundle = _cloud_bundle_for_sheet(sheets, sheet_id, folder_id=folder_id)
        if bundle is not None:
            cloud_matches.append((sheet_id, bundle))

    if program_name:
        named_cloud = [
            sheet_id
            for sheet_id, bundle in cloud_matches
            if str(
                (bundle.get("program") or {}).get("program_name", "")
                if isinstance(bundle.get("program"), Mapping)
                else ""
            ).strip() == program_name
        ]
        if len(named_cloud) == 1:
            return named_cloud[0]
        if len(named_cloud) > 1:
            raise ValueError(
                "La carpeta contiene más de un workbook Eira para el mismo programa; usa --sheet"
            )

    if len(cloud_matches) == 1:
        return cloud_matches[0][0]
    if len(cloud_matches) > 1:
        raise ValueError(
            "La carpeta contiene más de un workbook Eira válido; usa --sheet"
        )

    if not allow_legacy:
        return None

    prefix = f"Participación - {program_name}".strip()
    legacy_matches: list[str] = []
    required = {"Participantes", "Control", "Sesiones", "Programa"}
    for item in candidates:
        name = str(item.get("name", "")).strip()
        sheet_id = str(item.get("id", "")).strip()
        if not sheet_id or not name.startswith(prefix):
            continue
        titles = _sheet_titles(sheets, sheet_id)
        if required.issubset(titles):
            legacy_matches.append(sheet_id)
    if len(legacy_matches) == 1:
        return legacy_matches[0]
    if len(legacy_matches) > 1:
        raise ValueError(
            "Hay más de un workbook legacy compatible para el programa; usa --sheet"
        )
    return None


def setup_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Configura un programa Eira cloud-first sin workspace local persistente"
    )
    parser.add_argument("--drive-folder", required=True, help="URL de la carpeta Drive donde vive Eira")
    parser.add_argument("--spec", required=True, help="JSON de setup o '-' para stdin")
    parser.add_argument("--sheet", default="", help="URL/ID de un workbook Eira existente")
    parser.add_argument("--yes", action="store_true", help="confirma escrituras sin prompt interactivo")
    args = parser.parse_args(argv)
    try:
        spec = _load_json_arg(args.spec)
        program_raw = spec.get("program")
        if not isinstance(program_raw, Mapping):
            raise ValueError("spec.program debe ser un objeto")
        sessions = program_raw.get("sessions")
        if not isinstance(sessions, list) or not sessions:
            raise ValueError("spec.program.sessions requiere una lista no vacía")
        program_name = str(program_raw.get("program_name", "")).strip()
        if not program_name:
            raise ValueError("spec.program.program_name es obligatorio")
        roster_raw = spec.get("roster")
        if roster_raw is not None and not isinstance(roster_raw, Mapping):
            raise ValueError("spec.roster debe ser un objeto cuando se configura")
        default_mode = "official" if isinstance(roster_raw, Mapping) else "auto"
        participant_mode = str(program_raw.get("participant_mode", default_mode)).strip()
        if participant_mode not in {"official", "auto"}:
            raise ValueError("El flujo cloud-first soporta participant_mode=official o auto")
        if participant_mode == "official" and not isinstance(roster_raw, Mapping):
            raise ValueError("participant_mode=official requiere spec.roster")
        if participant_mode == "auto" and isinstance(roster_raw, Mapping):
            raise ValueError("Un roster vivo requiere participant_mode=official")

        folder_id = extract_folder_id(args.drive_folder)
        drive, sheets = get_google_services()
        metadata = validate_drive_folder(drive, folder_id)
        participants: list[dict[str, str]] = []
        if isinstance(roster_raw, Mapping):
            records, _ = _roster_records(sheets, roster_raw)
            participants = roster_records_to_participants(
                records, "google_sheets:live_roster"
            )
        existing_sheet_id = _find_sheet_in_folder(
            drive,
            sheets,
            folder_id,
            args.sheet,
            program_name=program_name,
            allow_legacy=True,
        )
        existing_bundle = (_cloud_bundle_for_sheet(
            sheets, existing_sheet_id, folder_id=folder_id
        ) if existing_sheet_id and hasattr(sheets, "spreadsheets") else None)
        existing_titles = (_sheet_titles(sheets, existing_sheet_id)
                           if existing_sheet_id and hasattr(sheets, "spreadsheets") else set())
        plan = build_plan(
            program_name=program_name,
            current_folder_name=program_name,  # cloud-first: no renombrar la carpeta del usuario
            folder_id=folder_id,
            folder_url=args.drive_folder,
            session_count=len(sessions),
            participant_mode=participant_mode,
            imported_participants=participants,
            existing_children={},
            existing_sheet_id=existing_sheet_id,
            evidence_sessions=sessions,
        )
        candidate_record = _candidate_record(
            folder_id, args.drive_folder, program_name, participant_mode, sessions,
            existing_sheet_id,
        )
        pilot = _normalize_pilot(
            spec.get("pilot") if isinstance(spec.get("pilot"), Mapping) else None,
            candidate_record,
        )
        candidate_program = program_from_dict(candidate_record)
        validate_session_mapping(pilot, candidate_program)
        existing_state = None
        if existing_sheet_id and "Estado" in existing_titles:
            existing_state = _cloud_json_value(sheets, existing_sheet_id, "Estado", "sync_state")
        _validate_processed_migration(existing_bundle, candidate_record, existing_state)
        if existing_sheet_id and hasattr(sheets, "spreadsheets"):
            _validate_program_view_schema(sheets, existing_sheet_id)
            _validate_legacy_processed_sessions(sheets, existing_sheet_id, sessions)
            _validate_workbook_capabilities(drive, existing_sheet_id)
        _validate_rule_lists({
            "known_external": list(spec.get("known_external", [])),
            "attendance_identity": list(spec.get("attendance_identity", [])),
        })
        known_external = spec.get("known_external", [])
        attendance_identity = spec.get("attendance_identity", [])
        if not isinstance(known_external, list) or not isinstance(attendance_identity, list):
            raise ValueError("known_external y attendance_identity deben ser listas")
        attendance_raw = pilot.get("attendance")
        attendance_identity_rows = list(spec.get("attendance_identity", []))
        if isinstance(attendance_raw, Mapping):
            if participant_mode == "auto" and not attendance_identity_rows:
                alias_source = attendance_raw.get("email_alias_source")
                if not isinstance(alias_source, Mapping):
                    raise ValueError(
                        "Attendance en modo auto requiere roster official o una política explícita de identidad"
                    )
            source = attendance_raw.get("source")
            mapping = attendance_raw.get("mapping")
            if not isinstance(source, Mapping) or not isinstance(mapping, Mapping):
                raise ValueError("pilot.attendance requiere source y mapping")
            attendance_table = _source_table(sheets, source)
            pilot_config = PilotConfig.from_dict(pilot)
            if pilot_config.attendance_mapping is None:
                raise ValueError("Attendance mapping ausente")
            mapping_result = map_table(attendance_table, pilot_config.attendance_mapping)
            if mapping_result.status is not MappingStatus.VALID:
                raise ValueError("Attendance mapping inválido: " + "; ".join(str(issue) for issue in mapping_result.issues))
            if isinstance(attendance_raw.get("email_alias_source"), Mapping):
                _validate_alias_source(sheets, attendance_raw)
                attendance_identity_rows.extend(_live_alias_rows(sheets, pilot))
            if participant_mode == "auto":
                validation_participants: list[Any] = list(participants)
                if not validation_participants and existing_sheet_id:
                    ids = _sheet_ids(sheets, existing_sheet_id)
                    validation_participants = list(ParticipantRepository(
                        GoogleSheetsValuesGateway(
                            sheets, existing_sheet_id, "Participantes", ids.get("Participantes")
                        )
                    ).load_read_only())
                _validate_auto_attendance_resolution(
                    attendance_identity_rows, mapping_result, validation_participants,
                )
        candidate_bundle: dict[str, Any] = {
            "version": CLOUD_BUNDLE_VERSION,
            "program": candidate_record,
            "pilot": pilot,
            "known_external": known_external,
            "attendance_identity": attendance_identity,
        }
        if isinstance(roster_raw, Mapping):
            candidate_bundle["roster"] = dict(roster_raw)
        if len(json.dumps(candidate_bundle, ensure_ascii=False).encode("utf-8")) > 900_000:
            raise ValueError("La configuración cloud supera el límite de 900 KB")
        print(f"Programa: {program_name}")
        print(f"Carpeta Drive: {args.drive_folder}")
        if participant_mode == "official":
            print(f"Participantes oficiales detectados: {len(participants)}")
        else:
            print("Roster externo: no configurado (modo auto)")
        print(f"Sesiones configuradas: {len(sessions)}")
        if not args.yes:
            answer = input("¿Confirmas crear/actualizar la configuración cloud de Eira? [s/N] ")
            if answer.strip().casefold() not in {"s", "si", "sí", "y", "yes"}:
                print("Cancelado; no se realizaron escrituras.")
                return 0

        record = execute_init(plan, drive, sheets, metadata, persist_local_registry=False)
        pilot = _normalize_pilot(pilot, record)
        if hasattr(sheets, "spreadsheets"):
            _reconcile_program_view(sheets, record["output"]["ref"], record, pilot)
        bundle: dict[str, Any] = {
            "version": CLOUD_BUNDLE_VERSION,
            "program": record,
            "pilot": pilot,
            "known_external": list(spec.get("known_external", [])),
            "attendance_identity": list(spec.get("attendance_identity", [])),
        }
        if isinstance(roster_raw, Mapping):
            bundle["roster"] = dict(roster_raw)
        _validate_rule_lists(bundle)
        configured_program = program_from_dict(record)
        GoogleSheetsControlRepository(
            sheets, record["output"]["ref"]
        ).ensure_sessions(configured_program.sessions)
        config_store = GoogleSheetsJsonStore(sheets, record["output"]["ref"], "Configuración")
        state_store = GoogleSheetsStateStore(
            GoogleSheetsJsonStore(sheets, record["output"]["ref"], "Estado")
        )
        config_store.put(CLOUD_BUNDLE_KEY, bundle)
        if state_store.store.get(state_store.key) is None:
            state_store.save({"version": 1, "programs": {}})
        else:
            state_store.load()  # validate existing authoritative state without resetting it
        print(json.dumps({
            "status": "READY",
            "program_id": record["program_id"],
            "sheet_id": record["output"]["ref"],
            "cloud_config": True,
            "cloud_state": True,
        }, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _refresh_live_roster(sheets: Any, sheet_id: str, roster_raw: Mapping[str, Any]) -> dict[str, int]:
    ids = _sheet_ids(sheets, sheet_id)
    repository = ParticipantRepository(
        GoogleSheetsValuesGateway(sheets, sheet_id, "Participantes", ids.get("Participantes"))
    )
    records, columns = _roster_records(sheets, roster_raw)
    records = _preserve_unspecified_roster_fields(records, repository.load_read_only(), columns)
    importer = RosterImporter(repository)
    plan = importer.dry_run(records)
    if plan.needs_review:
        raise ValueError(
            f"Roster live requiere revisión humana: {len(plan.needs_review)} reconciliaciones"
        )
    importer.apply(plan)
    return {
        "matched": sum(item.action == "MATCH" for item in plan.items),
        "updated": sum(item.action == "UPDATE" for item in plan.items),
        "created": sum(item.action == "CREATE" for item in plan.items),
    }


def _live_alias_rows(sheets: Any, pilot_raw: Mapping[str, Any]) -> list[dict[str, str]]:
    attendance = pilot_raw.get("attendance")
    if not isinstance(attendance, Mapping):
        return []
    alias_source = attendance.get("email_alias_source")
    if not isinstance(alias_source, Mapping):
        return []
    table = _source_table(sheets, alias_source)
    alias_column = str(alias_source.get("alias_column", "")).strip()
    canonical_column = str(alias_source.get("canonical_column", "")).strip()
    if not alias_column or not canonical_column:
        raise ValueError("email_alias_source requiere alias_column y canonical_column")
    rows: list[dict[str, str]] = []
    for item in table.rows:
        alias = str(item.values.get(alias_column, "") or "").strip()
        canonical = str(item.values.get(canonical_column, "") or "").strip()
        if not alias and not canonical:
            continue
        if not alias or not canonical:
            raise ValueError(
                f"email_alias_source fila {item.row_number} requiere alias y canonical"
            )
        rows.append({
            "external_id": alias,
            "status": "RESOLVED",
            "canonical_external_id": canonical,
            "reason": "explicit_email_alias",
            "provenance": f"{table.source_name}:{table.sheet_name}:row:{item.row_number}",
        })
    return rows


def _validate_alias_source(sheets: Any, attendance_raw: Mapping[str, Any]) -> None:
    source = attendance_raw.get("email_alias_source")
    if not isinstance(source, Mapping):
        return
    alias_column = str(source.get("alias_column", "")).strip()
    canonical_column = str(source.get("canonical_column", "")).strip()
    if not alias_column or not canonical_column:
        raise ValueError("email_alias_source requiere alias_column y canonical_column")
    table = _source_table(sheets, source)
    missing = [column for column in (alias_column, canonical_column) if column not in table.columns]
    if missing:
        raise ValueError("email_alias_source referencia columnas inexistentes: " + ", ".join(missing))


def _validate_auto_attendance_resolution(
    identity_rows: list[Mapping[str, Any]], mapping_result: Any,
    participants: list[Any],
) -> None:
    """Require every auto Attendance identity to resolve to an existing participant."""
    if not participants:
        raise ValueError(
            "Attendance en modo auto requiere participantes existentes y una política resoluble"
        )
    if not identity_rows:
        raise ValueError("Attendance en modo auto requiere una política de identidad resoluble")
    records: list[dict[str, Any]] = []
    for item in participants:
        records.append(dict(item) if isinstance(item, Mapping) else vars(item))
    resolver = ParticipantResolverAdapter(
        ParticipantResolver.official(records), match_email=True
    )
    policy = attendance_identity_policy_from_rows(identity_rows, resolver)
    mapped = {
        str(row.get("external_id", "") or "").strip().casefold()
        for row in identity_rows
        if str(row.get("external_id", "") or "").strip()
    }
    observed = {
        str(fact.participant_external_id).strip().casefold()
        for fact in mapping_result.facts
        if str(fact.participant_external_id).strip()
    }
    if observed - mapped:
        raise ValueError(
            "Attendance en modo auto tiene participantes sin mapping de identidad explícito"
        )
    for external_id in sorted(observed):
        result = policy.resolve(external_id)
        if result.status is not ResolutionStatus.RESOLVED:
            raise ValueError(
                "Attendance en modo auto tiene una identidad no resoluble: " + external_id
            )


def _write_known_external_csv(path: Path, rows: list[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["observed_name", "reason", "provenance"])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "observed_name": str(row.get("observed_name", "")),
                "reason": str(row.get("reason", "")),
                "provenance": str(row.get("provenance", "")),
            })


def run_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ejecuta Eira desde configuración y estado autoritativos en Google Workspace"
    )
    parser.add_argument("--drive-folder", required=True)
    parser.add_argument("--sheet", default="", help="URL/ID del Sheet Eira si la carpeta contiene más de uno")
    parser.add_argument("--sync-only", action="store_true")
    args = parser.parse_args(argv)
    lease = None
    try:
        folder_id = extract_folder_id(args.drive_folder)
        drive, sheets, _ = get_google_services_with_docs()
        validate_drive_folder(drive, folder_id)
        sheet_id = _find_sheet_in_folder(drive, sheets, folder_id, args.sheet)
        if sheet_id is None:
            raise ValueError(
                "No se encontró un workbook Eira válido en la carpeta; ejecuta eira-setup"
            )
        config_store = GoogleSheetsJsonStore(sheets, sheet_id, "Configuración")
        bundle = config_store.get(CLOUD_BUNDLE_KEY)
        if not isinstance(bundle, Mapping) or bundle.get("version") != CLOUD_BUNDLE_VERSION:
            raise ValueError("El Sheet no contiene una configuración cloud Eira válida")
        _validate_rule_lists(bundle)

        program_raw = bundle.get("program")
        roster_raw = bundle.get("roster")
        pilot_raw = bundle.get("pilot")
        if not isinstance(program_raw, Mapping):
            raise ValueError("Configuración cloud incompleta: program")
        if roster_raw is not None and not isinstance(roster_raw, Mapping):
            raise ValueError("Configuración cloud inválida: roster")
        if not isinstance(pilot_raw, Mapping):
            raise ValueError("Configuración cloud incompleta: pilot")
        program = program_from_dict(program_raw)
        if program.output.ref != sheet_id or program.source.ref != folder_id:
            raise ValueError("La configuración cloud no corresponde a la carpeta/Sheet solicitado")
        if program.participant_mode == "official" and not isinstance(roster_raw, Mapping):
            raise ValueError("Programa official sin roster cloud configurado")

        state_store = GoogleSheetsStateStore(
            GoogleSheetsJsonStore(sheets, sheet_id, "Estado")
        )
        lease = state_store.acquire_lease()

        if isinstance(roster_raw, Mapping):
            roster_result: Mapping[str, Any] = _refresh_live_roster(
                sheets, sheet_id, roster_raw
            )
        else:
            roster_result = {"mode": "auto", "source": "session_evidence"}
        GoogleSheetsControlRepository(sheets, sheet_id).ensure_sessions(program.sessions)
        known_rows = list(bundle.get("known_external", []))
        with tempfile.TemporaryDirectory(prefix="eira-cloud-") as directory:
            temp = Path(directory)
            program_for_runtime = dict(program_raw)
            if known_rows:
                known_path = temp / "known_external.csv"
                _write_known_external_csv(known_path, known_rows)
                program_for_runtime["known_external_path"] = str(known_path)
            programs_path = temp / "programs.json"
            save_programs(programs_path, {program.program_id: program_for_runtime})
            sync_result = build_runner(
                programs_path=programs_path,
                state_store=state_store,
                cloud_first=True,
            ).run(program.program_id)
            _print_results(sync_result)
            if any(item.sessions_failed > 0 or item.errors for item in sync_result):
                raise RuntimeError(
                    "Participation sync falló; se conserva la vista operacional anterior"
                )
            lease.verify()

            if args.sync_only:
                print(json.dumps({"roster": roster_result, "pilot": "SKIPPED"}, ensure_ascii=False))
                return 0

            pilot = PilotConfig.from_dict(pilot_raw)
            validate_session_mapping(pilot, program)
            google_source = ReadOnlyGoogleParticipationSource(sheets, sheet_id, {})
            participants = google_source.participants()
            participant_resolver: Any = build_participant_resolver(participants)
            identity_rows = list(bundle.get("attendance_identity", []))
            identity_rows.extend(_live_alias_rows(sheets, pilot_raw))
            if identity_rows:
                participant_resolver = attendance_identity_policy_from_rows(
                    identity_rows, participant_resolver
                )
            if (program.participant_mode == "auto" and pilot.attendance_mapping is not None
                    and not identity_rows):
                raise ValueError(
                    "Attendance en modo auto requiere una política de identidad explícita"
                )
            session_orders = {
                item.session_id: item.session_order for item in pilot.session_mapping.values()
            }
            applicability = RosterApplicabilityResolver(participants, session_orders)
            attendance_table = None
            if pilot.attendance_source is not None:
                table = _read_attendance_table(
                    temp / "cloud.json", pilot.attendance_source, sheets
                )
                attendance_table = lambda table=table: table
            result = PilotRunner(
                pilot,
                PilotSources(
                    google_source.scores,
                    attendance_table,
                    participant_resolver,
                    applicability,
                    participant_ids=lambda: [item.participant_id for item in participants],
                    participation_statuses=google_source.statuses,
                ),
            ).run()
            operational_view = build_operational_view(result)
            operational_repository = GoogleSheetsOperationalRepository(
                sheets, sheet_id,
                ParticipantRepository(GoogleSheetsValuesGateway(
                    sheets, sheet_id, "Participantes", _sheet_ids(sheets, sheet_id).get("Participantes")))
            )
            operational_repository.persist(operational_view)
            print(json.dumps({
                "roster": roster_result,
                "pilot": {
                    "summary": operational_view.summary(),
                    "cases": [case.to_dict() for case in operational_view.cases],
                    "signal_counts": dict(operational_view.signal_counts),
                    "as_of_session_id": operational_view.as_of_session_id,
                },
            }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if lease is not None:
            lease.release()


if __name__ == "__main__":
    raise SystemExit(run_main())
