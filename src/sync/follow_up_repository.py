from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Protocol, Sequence

from .follow_up import (FOLLOW_UP_RULESET_VERSION, FollowUpEntry, FollowUpLevel,
                        ProgramFollowUp, build_program_follow_up)
from .models import SessionRecord
from .participant_repository import execute_with_transient_retry
from .participants import Participant
from .scoring import ParticipantSessionScore

FOLLOW_UP_TITLE = "Seguimiento individual"
FOLLOW_UP_HEADERS = [
    "participant_id", "Participante", "Email", "Matrícula", "Sesiones elegibles",
    "Participó", "Frecuencia", "Últimas 4", "Última participación", "Score acumulado",
    "Seguimiento", "Desde", "Motivo", "Nota / Acción", "follow_up_ruleset_version",
]


@dataclass(frozen=True)
class FollowUpWriteResult:
    status: str
    rows_written: int
    critical_transitions: int = 0


class FollowUpGateway(Protocol):
    def ensure_sheet(self) -> tuple[bool, int | None]: ...
    def read_values(self) -> list[list[Any]]: ...
    def persist(self, values: list[list[Any]], manual_cells: set[tuple[int, int]],
                structural_change: bool, worksheet_id: int | None) -> FollowUpWriteResult: ...


class ControlRepository:
    def __init__(self, gateway: Any):
        self.gateway = gateway
        self.headers: list[str] = []

    def load_tracking_eligible(self) -> dict[int, bool]:
        values = self.gateway.read_values()
        if not values:
            return {}
        headers = [str(v).strip() for v in values[0]]
        keys = {value.casefold(): index for index, value in enumerate(headers)}
        column = keys.get("tracking_eligible")
        if column is None:
            column = len(headers)
            headers.append("tracking_eligible")
            self.gateway.write_cells(1, column + 1, [["tracking_eligible"]])
            for row_number, row in enumerate(values[1:], 2):
                if any(str(v).strip() for v in row):
                    self.gateway.write_cells(row_number, column + 1, [["Sí"]])
            values = self.gateway.read_values()
        self.headers = headers
        result: dict[int, bool] = {}
        session_column = keys.get("session_number", 0)
        for row in values[1:]:
            if session_column >= len(row) or not str(row[session_column]).strip():
                continue
            number = int(float(row[session_column]))
            value = str(row[column]).strip().casefold() if column < len(row) else "sí"
            if value in {"no", "false", "0"}:
                result[number] = False
            else:
                result[number] = True
        return result


class FollowUpRepository:
    def __init__(self, gateway: FollowUpGateway, participant_repository: Any,
                 session_results_repository: Any, control_repository: ControlRepository):
        self.gateway = gateway
        self.participant_repository = participant_repository
        self.session_results_repository = session_results_repository
        self.control_repository = control_repository

    def refresh(self, sessions: Sequence[SessionRecord], session_statuses: Mapping[int, str],
                *, official: bool) -> FollowUpWriteResult:
        if not official:
            return FollowUpWriteResult("NOOP", 0, 0)
        created, worksheet_id = self.gateway.ensure_sheet()
        existing = self.gateway.read_values()
        notes = self._notes(existing)
        tracking = self.control_repository.load_tracking_eligible()
        program = build_program_follow_up(
            self.participant_repository.load(), sessions, session_statuses, tracking,
            self.session_results_repository.load_scores(), official=True,
        )
        values, manual_cells = follow_up_values(program, notes)
        values, manual_cells = _preserve_unknown_columns(values, manual_cells, existing)
        structural = created or self._structure_differs(existing, values)
        result = self.gateway.persist(values, manual_cells, structural, worksheet_id)
        previous_levels = self._levels(existing)
        baseline = not bool(previous_levels) or self._previous_ruleset(existing) != FOLLOW_UP_RULESET_VERSION
        transitions = 0 if baseline else sum(
            entry.follow_up_level is FollowUpLevel.CRITICAL and previous_levels.get(entry.participant_id) != FollowUpLevel.CRITICAL
            for entry in program.entries if entry.enrollment_status != "inactive"
        )
        return FollowUpWriteResult(result.status, result.rows_written, transitions)

    @staticmethod
    def _notes(values: Sequence[Sequence[Any]]) -> dict[str, str]:
        if len(values) < 4 or values[3][:len(FOLLOW_UP_HEADERS)] != FOLLOW_UP_HEADERS:
            return {}
        return {str(row[0]).strip(): str(row[13]) if len(row) > 13 else ""
                for row in values[4:] if row and str(row[0]).strip()}

    @staticmethod
    def _levels(values: Sequence[Sequence[Any]]) -> dict[str, FollowUpLevel | None]:
        if len(values) < 4 or values[3][:len(FOLLOW_UP_HEADERS)] != FOLLOW_UP_HEADERS:
            return {}
        result: dict[str, FollowUpLevel | None] = {}
        for row in values[4:]:
            if not row or not str(row[0]).strip():
                continue
            value = str(row[10]).strip() if len(row) > 10 else ""
            result[str(row[0]).strip()] = next((level for level in FollowUpLevel if level.value == value), None)
        return result

    @staticmethod
    def _previous_ruleset(values: Sequence[Sequence[Any]]) -> int | None:
        if len(values) < 4 or values[3][:len(FOLLOW_UP_HEADERS)] != FOLLOW_UP_HEADERS:
            return None
        versions = {int(row[14]) for row in values[4:]
                    if len(row) > 14 and str(row[14]).strip().isdigit()}
        return versions.pop() if len(versions) == 1 else None

    @staticmethod
    def _structure_differs(existing: Sequence[Sequence[Any]], desired: Sequence[Sequence[Any]]) -> bool:
        return not existing or len(existing) < 4 or list(existing[3]) != list(desired[3]) or len(existing) != len(desired)


def follow_up_values(program: ProgramFollowUp, notes: Mapping[str, str]) -> tuple[list[list[Any]], set[tuple[int, int]]]:
    rows: list[list[Any]] = [
        [FOLLOW_UP_TITLE],
        ["Normal", program.normal_count, "Observar", program.observe_count,
         "Crítico", program.critical_count, "Sin historial", program.insufficient_history_count],
        ["● participación registrada · ○ sin participación registrada · no es asistencia ni evaluación"],
        list(FOLLOW_UP_HEADERS),
    ]
    manual_cells: set[tuple[int, int]] = set()
    for index, entry in enumerate(program.entries, 4):
        row = _entry_row(entry, notes.get(entry.participant_id, ""))
        rows.append(row)
        manual_cells.add((index, 13))
    width = len(FOLLOW_UP_HEADERS)
    return [row + [""] * (width - len(row)) for row in rows], manual_cells


def _preserve_unknown_columns(values: list[list[Any]], manual_cells: set[tuple[int, int]],
                              existing: Sequence[Sequence[Any]]) -> tuple[list[list[Any]], set[tuple[int, int]]]:
    if len(existing) < 4:
        return values, manual_cells
    extra_headers = [str(header) for header in existing[3][len(FOLLOW_UP_HEADERS):]]
    if not extra_headers:
        return values, manual_cells
    old_by_id = {str(row[0]).strip(): row for row in existing[4:] if row and str(row[0]).strip()}
    result = [list(row) for row in values]
    result[3].extend(extra_headers)
    for row_index in range(4, len(result)):
        old = old_by_id.get(str(result[row_index][0]).strip(), [])
        start = len(FOLLOW_UP_HEADERS)
        result[row_index].extend(list(old[start:start + len(extra_headers)]) + [""] * max(0, len(extra_headers) - len(old[start:])))
        manual_cells.update((row_index, start + offset) for offset in range(len(extra_headers)))
    return result, manual_cells


def _entry_row(entry: FollowUpEntry, note: str) -> list[Any]:
    return [
        entry.participant_id, entry.participant, entry.email, entry.enrollment_status,
        entry.eligible_sessions, entry.participation_sessions,
        entry.historical_frequency if entry.historical_frequency is not None else "—",
        entry.recent_window, f"Sesión {entry.last_participation_session}" if entry.last_participation_session else "Nunca",
        entry.score_total, entry.follow_up_level.value if entry.follow_up_level else "—",
        f"Sesión {entry.follow_up_since_session}" if entry.follow_up_since_session else "",
        entry.reason, note, entry.ruleset_version,
    ]


@dataclass
class GoogleSheetsFollowUpGateway:
    service: Any
    spreadsheet_id: str
    sheet_name: str = FOLLOW_UP_TITLE
    max_attempts: int = 3

    def ensure_sheet(self) -> tuple[bool, int | None]:
        metadata = execute_with_transient_retry(lambda: self.service.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id, includeGridData=False,
            fields="sheets(properties(title,sheetId,index))").execute(), self.max_attempts)
        for item in metadata.get("sheets", []):
            props = item.get("properties", {})
            if props.get("title") == self.sheet_name:
                return False, props.get("sheetId")
        response = execute_with_transient_retry(lambda: self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id, body={"requests": [{"addSheet": {"properties": {
                "title": self.sheet_name, "index": 1}}}]}).execute(), self.max_attempts)
        replies = response.get("replies", [])
        return True, (replies[0].get("addSheet", {}).get("properties", {}).get("sheetId") if replies else None)

    def read_values(self) -> list[list[Any]]:
        response = execute_with_transient_retry(lambda: self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id, range=f"'{self.sheet_name}'!A:ZZ").execute(), self.max_attempts)
        return response.get("values", [])

    def persist(self, values: list[list[Any]], manual_cells: set[tuple[int, int]],
                structural_change: bool, worksheet_id: int | None) -> FollowUpWriteResult:
        existing = self.read_values()
        payload = _json_values(values)
        if structural_change:
            self._clear_stale_tail(existing, values)
            execute_with_transient_retry(lambda: self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id, range=f"'{self.sheet_name}'!A1",
                valueInputOption="USER_ENTERED", body={"values": payload}).execute(), self.max_attempts)
            return FollowUpWriteResult("REPLACE", max(0, len(values) - 4))
        updates = []
        for row_index, row in enumerate(payload):
            old = existing[row_index] if row_index < len(existing) else []
            for col, value in enumerate(row):
                if (row_index, col) in manual_cells:
                    continue
                if (old[col] if col < len(old) else "") != value:
                    updates.append({"range": f"'{self.sheet_name}'!{_column(col + 1)}{row_index + 1}", "values": [[value]]})
        if not updates:
            return FollowUpWriteResult("NOOP", len(values) - 4)
        execute_with_transient_retry(lambda: self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=self.spreadsheet_id, body={"valueInputOption": "USER_ENTERED", "data": updates}).execute(), self.max_attempts)
        return FollowUpWriteResult("REPLACE", len(values) - 4)

    def _clear_stale_tail(self, existing: Sequence[Sequence[Any]],
                          desired: Sequence[Sequence[Any]]) -> None:
        if len(existing) <= len(desired):
            return
        width = max((len(row) for row in existing), default=0)
        width = max(width, max((len(row) for row in desired), default=0))
        if width == 0:
            return
        range_name = (f"'{self.sheet_name}'!A{len(desired) + 1}:"
                      f"{_column(width)}{len(existing)}")
        execute_with_transient_retry(lambda: self.service.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id, range=range_name, body={}).execute(), self.max_attempts)


def _json_values(values: Sequence[Sequence[Any]]) -> list[list[Any]]:
    return [[str(value) if isinstance(value, Decimal) else value for value in row] for row in values]


def _column(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result
