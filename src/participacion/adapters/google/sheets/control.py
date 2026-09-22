from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence, cast

from ....core.models import SessionInspection, SessionRecord
from ..retry import execute_with_transient_retry


class GoogleSheetsControlRepository:
    def __init__(self, service: Any, spreadsheet_id: str, sheet_name: str = "Control", max_attempts: int = 3):
        self.service = service
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        self.max_attempts = max_attempts

    def read_values(self) -> list[list[Any]]:
        response = execute_with_transient_retry(lambda: self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id, range=f"'{self.sheet_name}'!A:ZZ").execute(), self.max_attempts)
        return cast(list[list[Any]], response.get("values", []))

    def ensure_sessions(self, sessions: Sequence[SessionRecord]) -> str:
        """Non-destructively reconcile configured sessions into Control.

        Existing operational/manual values are preserved. Missing sessions are
        appended and only structural identity fields (name/state key) are
        refreshed for rows that already exist.
        """
        values = self.read_values()
        if not values:
            raise ValueError("Control no está inicializado")
        headers = [str(item).strip() for item in values[0]]
        positions = {name: index for index, name in enumerate(headers)}
        required = {
            "session_number", "session_name", "folder_id", "transcript_status",
            "chat_status", "processing_status", "last_processed_at",
        }
        if not required.issubset(positions):
            raise ValueError("Control requiere columnas: " + ", ".join(sorted(required)))

        rows_by_session: dict[int, int] = {}
        for row_number, row in enumerate(values[1:], 2):
            column = positions["session_number"]
            if column >= len(row) or not str(row[column]).strip():
                continue
            number = int(float(row[column]))
            if number in rows_by_session:
                raise ValueError(f"Control contiene session_number duplicado: {number}")
            rows_by_session[number] = row_number

        updates: list[dict[str, Any]] = []
        inserts: list[list[Any]] = []
        for session in sorted(sessions, key=lambda item: item.session_number):
            existing_row = rows_by_session.get(session.session_number)
            if existing_row is None:
                row = [""] * len(headers)
                desired = {
                    "session_number": session.session_number,
                    "session_name": session.session_name,
                    "folder_id": session.state_key,
                    "transcript_status": "pending",
                    "chat_status": "pending",
                    "processing_status": "pending",
                    "last_processed_at": "",
                }
                if "tracking_eligible" in positions:
                    desired["tracking_eligible"] = "Sí"
                for field, value in desired.items():
                    row[positions[field]] = value
                inserts.append(row)
                continue

            current = values[existing_row - 1]
            for field, value in (
                ("session_name", session.session_name),
                ("folder_id", session.state_key),
            ):
                column = positions[field]
                old = str(current[column]).strip() if column < len(current) else ""
                if old != str(value):
                    updates.append({
                        "range": f"'{self.sheet_name}'!{_column(column + 1)}{existing_row}",
                        "values": [[value]],
                    })

        if updates:
            execute_with_transient_retry(lambda: self.service.spreadsheets().values().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": updates},
            ).execute(), self.max_attempts)
        if inserts:
            execute_with_transient_retry(lambda: self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{self.sheet_name}'!A:ZZ",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": inserts},
            ).execute(), self.max_attempts)
        return "REPLACE" if updates or inserts else "NOOP"

    def reconcile(self, inspections: Sequence[SessionInspection],
                  statuses: Mapping[int, str], processed_at: Mapping[int, str]) -> str:
        values = self.read_values()
        if not values:
            return "NOOP"
        headers = [str(item).strip() for item in values[0]]
        positions = {name: index for index, name in enumerate(headers)}
        required = {"session_number", "transcript_status", "chat_status", "processing_status", "last_processed_at"}
        if not required.issubset(positions):
            raise ValueError("Control requiere columnas: " + ", ".join(sorted(required)))
        rows_by_session: dict[int, int] = {}
        for row_number, row in enumerate(values[1:], 2):
            if positions["session_number"] >= len(row) or not str(row[positions["session_number"]]).strip():
                continue
            number = int(float(row[positions["session_number"]]))
            if number in rows_by_session:
                raise ValueError(f"Control contiene session_number duplicado: {number}")
            rows_by_session[number] = row_number
        updates: list[dict[str, Any]] = []
        for inspection in inspections:
            number = inspection.session.session_number
            sheet_row = rows_by_session.get(number)
            if sheet_row is None:
                raise ValueError(
                    f"Control no contiene la sesión configurada {number}; ejecuta reconciliación estructural"
                )
            evidence = set(inspection.evidence_contexts.values())
            desired = {
                "transcript_status": "present" if any(item and item.evidence_type == "transcript" for item in evidence) else "missing",
                "chat_status": "present" if any(item and item.evidence_type == "chat" for item in evidence) else "missing",
            }
            current = values[sheet_row - 1]
            for field, value in desired.items():
                column = positions[field]
                old = str(current[column]).strip() if column < len(current) else ""
                if old != value:
                    updates.append({"range": f"'{self.sheet_name}'!{_column(column + 1)}{sheet_row}", "values": [[value]]})
            status = statuses.get(number)
            if status and status != "SKIPPED":
                column = positions["processing_status"]
                old = str(current[column]).strip() if column < len(current) else ""
                if old != status:
                    updates.append({"range": f"'{self.sheet_name}'!{_column(column + 1)}{sheet_row}", "values": [[status]]})
                timestamp = processed_at.get(number)
                if timestamp:
                    column = positions["last_processed_at"]
                    old = str(current[column]).strip() if column < len(current) else ""
                    if old != timestamp:
                        updates.append({"range": f"'{self.sheet_name}'!{_column(column + 1)}{sheet_row}", "values": [[timestamp]]})
        if not updates:
            return "NOOP"
        execute_with_transient_retry(lambda: self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute(), self.max_attempts)
        return "REPLACE"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _column(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result
