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
from ..application.pilot.runner import PilotRunner, PilotSources, RosterApplicabilityResolver
from ..application.registry import program_from_dict, save_programs
from ..application.roster import RosterImporter, RosterRecord, roster_records_from_rows, roster_records_to_participants
from ..core.participants import Participant
from .main import _print_results, _sheet_ids, build_runner
from .pilot import _aggregate, _read_attendance_table


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
    titles = _sheet_titles(sheets, spreadsheet_id)
    if "Configuración" not in titles:
        return None
    bundle = GoogleSheetsJsonStore(
        sheets, spreadsheet_id, "Configuración"
    ).get(CLOUD_BUNDLE_KEY)
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


def _find_sheet_in_folder(
    drive: Any,
    sheets: Any,
    folder_id: str,
    sheet_value: str = "",
    *,
    program_name: str = "",
    allow_legacy: bool = False,
) -> str | None:
    if sheet_value.strip():
        return extract_spreadsheet_id(sheet_value)

    candidates = [
        item for item in list_children(drive, folder_id)
        if item.get("mimeType") == SHEET_MIME
    ]
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
        record.pop("created_at", None)
        pilot = _normalize_pilot(
            spec.get("pilot") if isinstance(spec.get("pilot"), Mapping) else None,
            record,
        )
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

        if isinstance(roster_raw, Mapping):
            roster_result: Mapping[str, Any] = _refresh_live_roster(
                sheets, sheet_id, roster_raw
            )
        else:
            roster_result = {"mode": "auto", "source": "session_evidence"}
        GoogleSheetsControlRepository(sheets, sheet_id).ensure_sessions(program.sessions)
        state_store = GoogleSheetsStateStore(
            GoogleSheetsJsonStore(sheets, sheet_id, "Estado")
        )

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
            ).run(program.program_id)
            _print_results(sync_result)

            if args.sync_only:
                print(json.dumps({"roster": roster_result, "pilot": "SKIPPED"}, ensure_ascii=False))
                return 0

            pilot = PilotConfig.from_dict(pilot_raw)
            google_source = ReadOnlyGoogleParticipationSource(sheets, sheet_id, {})
            participants = google_source.participants()
            participant_resolver: Any = build_participant_resolver(participants)
            identity_rows = list(bundle.get("attendance_identity", []))
            identity_rows.extend(_live_alias_rows(sheets, pilot_raw))
            if identity_rows:
                participant_resolver = attendance_identity_policy_from_rows(
                    identity_rows, participant_resolver
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
            print(json.dumps({
                "roster": roster_result,
                "pilot": _aggregate(result),
            }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run_main())
