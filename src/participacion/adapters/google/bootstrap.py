#!/usr/bin/env python3
"""Inicializa un programa de seguimiento de participación.

La fase init solo prepara Drive, Sheets y el registro local. No analiza
transcripts/chats, no usa un LLM y no calcula participación.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from ...application.init_program import InitPlan, build_plan, validate_session_count
from ...application.registry import load_programs, save_programs
from ...application.roster import roster_records_from_rows, roster_records_to_participants
from .sheets.schema import (CLOUD_JSON_HEADERS, CONTROL_HEADERS, PARTICIPANT_HEADERS,
                            PROGRAM_HEADERS, RANKING_HEADERS, REQUIRED_SHEETS, SESSION_HEADERS)
from ..roster.csv_source import read_csv_roster
from ..roster.google_source import read_google_roster
from ..roster.xlsx_source import read_xlsx_roster

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = Path(os.environ.get(
    "PARTICIPACION_CONFIG_DIR", Path.home() / ".config" / "participacion"
)) / "programs.json"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"



def extract_folder_id(url: str) -> str:
    """Extract a Google Drive folder ID without changing it."""
    value = url.strip()
    match = re.search(r"/folders/([A-Za-z0-9_-]+)(?:[/?#]|$)", value)
    if not match:
        match = re.search(r"[?&]id=([A-Za-z0-9_-]+)(?:&|$)", value)
    if not match:
        raise ValueError("La URL no contiene un ID de carpeta de Google Drive válido")
    return match.group(1)


def _header_key(value: Any) -> str:
    return str(value).strip().casefold()


def load_participants_from_rows(rows: list[list[Any]], source: str) -> list[dict[str, str]]:
    if not rows:
        raise ValueError("La lista de participantes está vacía")
    return roster_records_to_participants(roster_records_from_rows(rows), source)


def extract_spreadsheet_id(source: str) -> str:
    value = source.strip()
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)(?:[/?#]|$)", value)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", value):
        return value
    raise ValueError("El origen no contiene un ID o URL de Google Sheet válido")


def load_participants(source: str, sheets_service: Any = None) -> list[dict[str, str]]:
    path = Path(source).expanduser()
    if path.is_file() and path.suffix.casefold() == ".csv":
        return roster_records_to_participants(read_csv_roster(path), str(path))
    if path.is_file() and path.suffix.casefold() == ".xlsx":
        return roster_records_to_participants(read_xlsx_roster(path), str(path))
    if sheets_service is not None:
        spreadsheet_id = extract_spreadsheet_id(source)
        class GoogleSource:
            def read_values(self) -> list[list[Any]]:
                return sheets_service.spreadsheets().values().get(
                    spreadsheetId=spreadsheet_id, range="A:Z"
                ).execute().get("values", [])
        return roster_records_to_participants(read_google_roster(GoogleSource()), spreadsheet_id)
    raise ValueError("El origen debe ser un archivo CSV/XLSX o el ID de un Google Sheet")


def _get_google_credentials() -> Any:
    from .auth import load_credentials
    return load_credentials()


def get_google_services() -> tuple[Any, Any]:
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Instala las dependencias Google: pip install 'participacion-agent[google]'") from exc
    credentials = _get_google_credentials()
    return build("drive", "v3", credentials=credentials), build("sheets", "v4", credentials=credentials)


def get_google_services_with_docs() -> tuple[Any, Any, Any]:
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Instala las dependencias Google: pip install 'participacion-agent[google]'") from exc
    credentials = _get_google_credentials()
    return (
        build("drive", "v3", credentials=credentials),
        build("sheets", "v4", credentials=credentials),
        build("docs", "v1", credentials=credentials),
    )


def validate_drive_folder(drive: Any, folder_id: str) -> dict[str, Any]:
    metadata = drive.files().get(
        fileId=folder_id,
        fields="id,name,mimeType,capabilities(canAddChildren,canEdit),permissions(id,type,role)",
        supportsAllDrives=True,
    ).execute()
    if metadata.get("mimeType") != FOLDER_MIME:
        raise ValueError("El recurso indicado no es una carpeta de Google Drive")
    capabilities = metadata.get("capabilities", {})
    if capabilities and (not capabilities.get("canAddChildren") or not capabilities.get("canEdit")):
        raise PermissionError("La carpeta no permite crear y editar el workbook Eira")
    return metadata


def list_children(drive: Any, folder_id: str) -> list[dict[str, Any]]:
    response = drive.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        fields="files(id,name,mimeType,parents)", pageSize=1000, supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    return response.get("files", [])


def _find_existing_sheet(children: list[dict[str, Any]], title: str) -> str | None:
    return next((item["id"] for item in children
                 if item.get("name") == title and item.get("mimeType") == SHEET_MIME), None)


def _confirm(prompt: str = "¿Confirmas realizar todas las escrituras? [s/N] ") -> bool:
    return input(prompt).strip().casefold() in {"s", "si", "sí", "y", "yes"}


def _print_summary(plan: InitPlan) -> None:
    imported = f"{len(plan.participants)}" if plan.participant_mode == "import" else "no aplica"
    pending = sum(create for _, _, create in plan.session_folders)
    print(f"Programa: {plan.program_name}")
    print(f"Carpeta: {plan.folder_url}")
    print(f"Sesiones: {plan.session_count}")
    print(f"Modo participantes: {plan.participant_mode}")
    if plan.current_folder_name and plan.current_folder_name != plan.program_name:
        print(f"Renombrar carpeta: {plan.current_folder_name} → {plan.program_name}")
    if plan.participant_mode in {"import", "official"}:
        print(f"Participantes importados: {imported}")
    print("\nSe crearán:")
    print(f"- {pending} carpetas de sesión")
    print(f"- {'1' if plan.create_sheet else '0'} Google Sheet (se reutiliza el existente si corresponde)")


def _sheet_values(plan: InitPlan, session_records: list[dict[str, Any]]) -> dict[str, list[list[Any]]]:
    created_at = dt.datetime.now(dt.timezone.utc).isoformat()
    from ...application.tracking import TrackingSession, TrackingView, tracking_values
    from .sheets.follow_up import FOLLOW_UP_HEADERS
    tracking = tracking_values(TrackingView((), tuple(
        TrackingSession(int(record["session_number"]), record["session_name"], "PENDING", None, None, None, {})
        for record in session_records
    )))[0]
    return {
        "Seguimiento": tracking,
        "Seguimiento individual": [["SEGUIMIENTO INDIVIDUAL"], ["Normal", 0, "Observar", 0, "Crítico", 0, "Sin historial", 0], ["● participación registrada · ○ sin participación registrada · no es asistencia ni evaluación"], FOLLOW_UP_HEADERS],
        "Programa": [PROGRAM_HEADERS, [plan.folder_id, plan.program_name, plan.session_count,
                                       plan.participant_mode, "google_drive", plan.folder_id,
                                       plan.folder_url, "google_sheets", getattr(plan, "sheet_id", "") or "",
                                       created_at, 1]],
        "Sesiones": [SESSION_HEADERS],
        "Participantes": [PARTICIPANT_HEADERS] + [[p.get(h, "participant" if h == "role" else "") for h in PARTICIPANT_HEADERS] for p in plan.participants],
        "Ranking": [RANKING_HEADERS],
        "Control": [CONTROL_HEADERS] + [[int(record["session_number"]), record["session_name"],
                                          record.get("state_key", record.get("source_ref", record.get("folder_id", ""))),
                                          "pending", "pending", "pending", "", "Sí"]
                                         for record in session_records],
        "Configuración": [CLOUD_JSON_HEADERS],
        "Estado": [CLOUD_JSON_HEADERS],
    }


def _ensure_sheet(sheets: Any, drive: Any, plan: InitPlan, children: list[dict[str, Any]],
                  session_records: list[dict[str, Any]]) -> str:
    title = f"Participación - {plan.program_name} - Eira Piloto"
    newly_created = plan.sheet_id is None
    added_sheets: list[str] = []
    if plan.sheet_id:
        sheet_id = plan.sheet_id
        existing = sheets.spreadsheets().get(spreadsheetId=sheet_id, includeGridData=False).execute()
        names = [item["properties"]["title"] for item in existing.get("sheets", [])]
        added_sheets = [name for name in REQUIRED_SHEETS if name not in names]
        requests = [{"addSheet": {"properties": {"title": name}}}
                    for name in added_sheets]
        if requests:
            sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": requests}).execute()
    else:
        created = sheets.spreadsheets().create(body={"properties": {"title": title},
                                                    "sheets": [{"properties": {"title": "Seguimiento"}}]}).execute()
        sheet_id = created["spreadsheetId"]
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": name}}}
                                for name in REQUIRED_SHEETS[1:]]},
        ).execute()
        drive.files().update(fileId=sheet_id, addParents=plan.folder_id,
                             fields="id,parents", supportsAllDrives=True).execute()
    values = _sheet_values(plan, session_records)
    # Un workbook nuevo recibe todas las hojas. En uno existente solo se
    # inicializan las hojas que acaban de agregarse; nunca se sobrescriben
    # datos ya presentes.
    target_sheets = list(values) if newly_created else added_sheets
    for sheet_name in target_sheets:
        rows = values.get(sheet_name, [])
        if rows:
            sheets.spreadsheets().values().update(
                spreadsheetId=sheet_id, range=f"'{sheet_name}'!A1",
                valueInputOption="RAW", body={"values": rows},
            ).execute()
    return sheet_id


def execute_init(plan: InitPlan, drive: Any, sheets: Any, folder_metadata: dict[str, Any],
                 *, persist_local_registry: bool = True) -> dict[str, Any]:
    """Perform writes only after the caller has received confirmation.

    Cloud-first callers can disable the legacy local registry. The returned
    program record is then persisted in the Eira workbook instead.
    """
    children = list_children(drive, plan.folder_id)
    if plan.current_folder_name != plan.program_name:
        drive.files().update(fileId=plan.folder_id, body={"name": plan.program_name},
                             fields="id,name", supportsAllDrives=True).execute()
    session_records = []
    if plan.source_mode == "evidence_sources":
        session_records = [dict(item) for item in plan.evidence_sessions]
        for record in session_records:
            record["state_key"] = record["session_id"]
    else:
        for name, existing_id, should_create in plan.session_folders:
            folder_id = existing_id
            if should_create:
                created = drive.files().create(
                    body={"name": name, "mimeType": FOLDER_MIME, "parents": [plan.folder_id]},
                    fields="id,name,parents", supportsAllDrives=True,
                ).execute()
                folder_id = created["id"]
            session_records.append({"session_number": len(session_records) + 1,
                                   "session_name": name, "source_ref": folder_id or "",
                                   "state_key": folder_id or ""})
    sheet_id = _ensure_sheet(sheets, drive, plan, children, session_records)
    from participacion.adapters.google.sheets.participants import GoogleSheetsValuesGateway, ParticipantRepository
    from participacion.adapters.google.sheets.styling import GoogleSheetStyler
    metadata = sheets.spreadsheets().get(spreadsheetId=sheet_id, includeGridData=False,
                                         fields="sheets(properties(title,sheetId))").execute()
    ids = {item["properties"]["title"]: item["properties"]["sheetId"]
           for item in metadata.get("sheets", [])}
    GoogleSheetStyler(sheets, sheet_id).apply()
    if ids.get("Participantes") is not None:
        ParticipantRepository(GoogleSheetsValuesGateway(
            sheets, sheet_id, "Participantes", ids["Participantes"])).migrate()
    record = {"program_id": plan.folder_id, "program_name": plan.program_name,
              "session_count": plan.session_count, "participant_mode": plan.participant_mode,
              "source": {"provider": "google_drive", "ref": plan.folder_id,
                         "metadata": {"url": plan.folder_url}},
              "output": {"provider": "google_sheets", "ref": sheet_id},
              "sessions": session_records, "created_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if persist_local_registry:
        existing = load_programs(REGISTRY_PATH)
        existing[plan.folder_id] = record
        save_programs(REGISTRY_PATH, existing)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inicializa un programa de participación")
    parser.parse_args(argv)
    try:
        folder_url = input("URL de carpeta Google Drive: ").strip()
        folder_id = extract_folder_id(folder_url)
        program_name = input("Nombre del programa: ").strip()
        session_count = validate_session_count(input("Número de sesiones: "))
        mode = input("Modo de participantes (A=auto, B=import, C=official): ").strip().casefold()
        participant_mode = {"a": "auto", "auto": "auto", "b": "import", "import": "import", "c": "official", "official": "official"}.get(mode)
        if participant_mode is None:
            raise ValueError("El modo debe ser A/auto, B/import o C/official")
        services = None
        participants = []
        if participant_mode in {"import", "official"}:
            source = input("Origen de la lista (CSV, XLSX o Google Sheet): ").strip()
            drive, sheets = get_google_services()
            services = (drive, sheets)
            participants = load_participants(source, sheets)
            print(f"Participantes detectados: {len(participants)}")
        else:
            services = services or get_google_services()
        drive, sheets = services
        metadata = validate_drive_folder(drive, folder_id)
        children = list_children(drive, folder_id)
        existing_children = {item["name"]: item["id"] for item in children if item.get("mimeType") == FOLDER_MIME}
        sheet_id = _find_existing_sheet(children, f"Participación - {program_name}")
        plan = build_plan(program_name=program_name, current_folder_name=metadata.get("name", ""),
                          folder_id=folder_id, folder_url=folder_url,
                          session_count=session_count, participant_mode=participant_mode,
                          imported_participants=participants, existing_children=existing_children,
                          existing_sheet_id=sheet_id)
        _print_summary(plan)
        if not _confirm():
            print("Cancelado; no se realizaron escrituras.")
            return 0
        record = execute_init(plan, drive, sheets, metadata)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
