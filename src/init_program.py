#!/usr/bin/env python3
"""Inicializa un programa de seguimiento de participación.

La fase init solo prepara Drive, Sheets y el registro local. No analiza
transcripts/chats, no usa un LLM y no calcula participación.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / ".participation_tracker" / "programs.json"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
REQUIRED_SHEETS = ["Programa", "Sesiones", "Participantes", "Control"]
PARTICIPANT_HEADERS = ["participant_id", "nombre", "correo", "aliases", "role", "source", "status"]
SESSION_HEADERS = [
    "session_number", "session_name", "participant", "email", "voice_total",
    "voice_valid", "chat_total", "chat_valid", "score",
]
CONTROL_HEADERS = [
    "session_number", "session_name", "folder_id", "transcript_status",
    "chat_status", "processing_status", "last_processed_at",
]
PROGRAM_HEADERS = [
    "nombre_programa", "folder_id", "folder_url", "session_count",
    "participant_mode", "created_at",
]


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


def extract_folder_id(url: str) -> str:
    """Extract a Google Drive folder ID without changing it."""
    value = url.strip()
    match = re.search(r"/folders/([A-Za-z0-9_-]+)(?:[/?#]|$)", value)
    if not match:
        match = re.search(r"[?&]id=([A-Za-z0-9_-]+)(?:&|$)", value)
    if not match:
        raise ValueError("La URL no contiene un ID de carpeta de Google Drive válido")
    return match.group(1)


def validate_session_count(value: str | int) -> int:
    text = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise ValueError("El número de sesiones debe ser un entero mayor que cero")
    return int(text)


def session_folder_name(number: int) -> str:
    if number < 1:
        raise ValueError("El número de sesión debe ser positivo")
    return f"{number:02d} - Sesión {number}"


def _header_key(value: Any) -> str:
    return str(value).strip().casefold()


def validate_participant_columns(headers: Iterable[Any]) -> dict[str, int]:
    positions = {_header_key(value): index for index, value in enumerate(headers)}
    missing = [key for key in ("nombre", "correo") if key not in positions]
    if missing:
        raise ValueError("Faltan columnas obligatorias: " + ", ".join(missing))
    return {key: positions[key] for key in ("nombre", "correo")}


def build_plan(*, program_name: str, folder_id: str, folder_url: str,
               session_count: int, participant_mode: str,
               imported_participants: list[dict[str, str]],
               existing_children: dict[str, str],
               existing_sheet_id: str | None,
               current_folder_name: str = "") -> InitPlan:
    if not program_name.strip():
        raise ValueError("El nombre del programa no puede estar vacío")
    session_count = validate_session_count(session_count)
    if participant_mode not in {"auto", "import"}:
        raise ValueError("El modo de participantes debe ser auto o import")
    participants = list(imported_participants) if participant_mode == "import" else []
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


def _read_csv(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.reader(handle))


def _xlsx_cell_value(cell: ET.Element, shared: list[str]) -> str:
    value = cell.find("{*}v")
    raw = "" if value is None else value.text or ""
    if cell.attrib.get("t") == "s" and raw.isdigit() and int(raw) < len(shared):
        return shared[int(raw)]
    return raw


def _read_xlsx(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.itertext()) for node in root.findall(".//{*}si")]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet = workbook.find(".//{*}sheet")
        if sheet is None:
            return []
        rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next((item.attrib["Target"] for item in rels.findall("{*}Relationship")
                       if item.attrib.get("Id") == rel_id), "worksheets/sheet1.xml")
        target = target.removeprefix("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        root = ET.fromstring(archive.read(target))
        rows = []
        for row in root.findall(".//{*}row"):
            cells = {}
            for cell in row.findall("{*}c"):
                ref = cell.attrib.get("r", "A1")
                column = re.match(r"[A-Z]+", ref).group(0)
                index = 0
                for char in column:
                    index = index * 26 + ord(char) - 64
                cells[index - 1] = _xlsx_cell_value(cell, shared)
            rows.append([cells.get(i, "") for i in range(max(cells, default=-1) + 1)])
        return rows


def load_participants_from_rows(rows: list[list[Any]], source: str) -> list[dict[str, str]]:
    if not rows:
        raise ValueError("La lista de participantes está vacía")
    mapping = validate_participant_columns(rows[0])
    participants = []
    for row_number, row in enumerate(rows[1:], start=2):
        values = list(row) + [""] * (max(mapping.values()) + 1 - len(row))
        name, email = (str(values[mapping[key]]) for key in ("nombre", "correo"))
        if not name.strip() or not email.strip():
            raise ValueError(f"La fila {row_number} requiere nombre y correo")
        participants.append({
            "participant_id": "", "nombre": name, "correo": email, "aliases": "",
            "role": "participant", "source": source, "status": "new",
        })
    return participants


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
        return load_participants_from_rows(_read_csv(path), str(path))
    if path.is_file() and path.suffix.casefold() == ".xlsx":
        return load_participants_from_rows(_read_xlsx(path), str(path))
    if sheets_service is not None:
        spreadsheet_id = extract_spreadsheet_id(source)
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range="A:Z"
        ).execute()
        return load_participants_from_rows(result.get("values", []), spreadsheet_id)
    raise ValueError("El origen debe ser un archivo CSV/XLSX o el ID de un Google Sheet")


def get_google_services() -> tuple[Any, Any]:
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Faltan dependencias de Google Workspace") from exc
    token = Path.home() / ".hermes" / "google_token.json"
    if not token.exists():
        raise RuntimeError(f"No existe el token OAuth: {token}")
    credentials = Credentials.from_authorized_user_file(str(token))
    return build("drive", "v3", credentials=credentials), build("sheets", "v4", credentials=credentials)


def validate_drive_folder(drive: Any, folder_id: str) -> dict[str, Any]:
    metadata = drive.files().get(
        fileId=folder_id,
        fields="id,name,mimeType,capabilities(canAddChildren,canEdit),permissions(id,type,role)",
        supportsAllDrives=True,
    ).execute()
    if metadata.get("mimeType") != FOLDER_MIME:
        raise ValueError("El recurso indicado no es una carpeta de Google Drive")
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
    if plan.participant_mode == "import":
        print(f"Participantes importados: {imported}")
    print("\nSe crearán:")
    print(f"- {pending} carpetas de sesión")
    print(f"- {'1' if plan.create_sheet else '0'} Google Sheet (se reutiliza el existente si corresponde)")


def _sheet_values(plan: InitPlan, session_records: list[dict[str, Any]]) -> dict[str, list[list[Any]]]:
    created_at = dt.datetime.now(dt.timezone.utc).isoformat()
    return {
        "Programa": [PROGRAM_HEADERS, [plan.program_name, plan.folder_id, plan.folder_url,
                                         str(plan.session_count), plan.participant_mode, created_at]],
        "Sesiones": [SESSION_HEADERS],
        "Participantes": [PARTICIPANT_HEADERS] + [[p.get(h, "participant" if h == "role" else "") for h in PARTICIPANT_HEADERS] for p in plan.participants],
        "Control": [CONTROL_HEADERS] + [[int(record["session_number"]), record["session_name"], record["folder_id"],
                                          "pending", "pending", "pending", ""]
                                         for record in session_records],
    }


def _ensure_sheet(sheets: Any, drive: Any, plan: InitPlan, children: list[dict[str, Any]],
                  session_records: list[dict[str, Any]]) -> str:
    title = f"Participación - {plan.program_name}"
    newly_created = plan.sheet_id is None
    if plan.sheet_id:
        sheet_id = plan.sheet_id
        existing = sheets.spreadsheets().get(spreadsheetId=sheet_id, includeGridData=False).execute()
        names = [item["properties"]["title"] for item in existing.get("sheets", [])]
        extras = [name for name in names if name not in REQUIRED_SHEETS]
        if extras:
            raise RuntimeError("El Sheet existente contiene hojas adicionales: " + ", ".join(extras))
        requests = [{"addSheet": {"properties": {"title": name}}}
                    for name in REQUIRED_SHEETS if name not in names]
        if requests:
            sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": requests}).execute()
    else:
        created = sheets.spreadsheets().create(body={"properties": {"title": title},
                                                    "sheets": [{"properties": {"title": "Programa"}}]}).execute()
        sheet_id = created["spreadsheetId"]
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": name}}}
                                for name in REQUIRED_SHEETS[1:]]},
        ).execute()
        drive.files().update(fileId=sheet_id, addParents=plan.folder_id,
                             fields="id,parents", supportsAllDrives=True).execute()
    # Solo inicializar valores en un Sheet recién creado. Un Sheet existente
    # se inspecciona y, como máximo, recibe pestañas faltantes; nunca se
    # sobrescribe su contenido.
    if newly_created:
        values = _sheet_values(plan, session_records)
        for sheet_name, rows in values.items():
            sheets.spreadsheets().values().update(
                spreadsheetId=sheet_id, range=f"'{sheet_name}'!A1",
                valueInputOption="RAW", body={"values": rows},
            ).execute()
    return sheet_id


def execute_init(plan: InitPlan, drive: Any, sheets: Any, folder_metadata: dict[str, Any]) -> dict[str, Any]:
    """Perform writes only after the caller has received confirmation."""
    children = list_children(drive, plan.folder_id)
    if plan.current_folder_name != plan.program_name:
        drive.files().update(fileId=plan.folder_id, body={"name": plan.program_name},
                             fields="id,name", supportsAllDrives=True).execute()
    session_records = []
    for name, existing_id, should_create in plan.session_folders:
        folder_id = existing_id
        if should_create:
            created = drive.files().create(
                body={"name": name, "mimeType": FOLDER_MIME, "parents": [plan.folder_id]},
                fields="id,name,parents", supportsAllDrives=True,
            ).execute()
            folder_id = created["id"]
        session_records.append({"session_number": len(session_records) + 1,
                               "session_name": name, "folder_id": folder_id or ""})
    sheet_id = _ensure_sheet(sheets, drive, plan, children, session_records)
    record = {"program_name": plan.program_name, "folder_id": plan.folder_id,
              "folder_url": plan.folder_url, "session_count": plan.session_count,
              "participant_mode": plan.participant_mode, "sheet_id": sheet_id,
              "sessions": session_records, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(REGISTRY_PATH.read_text(encoding="utf-8")) if REGISTRY_PATH.exists() else {}
    existing[plan.folder_id] = record
    REGISTRY_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inicializa un programa de participación")
    parser.parse_args(argv)
    try:
        folder_url = input("URL de carpeta Google Drive: ").strip()
        folder_id = extract_folder_id(folder_url)
        program_name = input("Nombre del programa: ").strip()
        session_count = validate_session_count(input("Número de sesiones: "))
        mode = input("Modo de participantes (A=auto, B=import): ").strip().casefold()
        participant_mode = {"a": "auto", "auto": "auto", "b": "import", "import": "import"}.get(mode)
        if participant_mode is None:
            raise ValueError("El modo debe ser A/auto o B/import")
        services = None
        participants = []
        if participant_mode == "import":
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
