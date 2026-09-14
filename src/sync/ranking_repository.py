from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .participant_repository import execute_with_transient_retry
from .participants import Participant
from .ranking import ProgramRanking


def _key(value):
    return str(value).strip().casefold()


CANONICAL_RANKING_HEADERS = [
    "rank", "participant_id", "participant", "email", "sessions_with_activity",
    "voice_total", "voice_valid_total", "chat_total", "chat_valid_total",
    "score_total", "ranking_complete",
]
CANONICAL_RANKING_KEYS = {_key(header) for header in CANONICAL_RANKING_HEADERS}
NUMERIC_RANKING_KEYS = {
    "rank", "sessions_with_activity", "voice_total", "voice_valid_total",
    "chat_total", "chat_valid_total", "score_total",
}


class RankingGateway(Protocol):
    def inspect_sheet(self) -> Mapping[str, Any] | None: ...
    def read_values(self) -> list[list[Any]]: ...
    def write_cells(self, start_row: int, start_column: int, values: list[list[Any]]) -> None: ...
    def apply_changes(
        self, updates: Mapping[int, Mapping[int, Any]], inserts: Sequence[Sequence[Any]], deletes: Sequence[int]
    ) -> None: ...
    def reorder_rows(self, participant_ids: Sequence[str]) -> None: ...


@dataclass(frozen=True)
class RankingWriteResult:
    status: str
    rows_written: int


@dataclass
class GoogleSheetsRankingGateway:
    service: Any
    spreadsheet_id: str
    worksheet_id: int | None = None
    sheet_name: str = "Ranking"
    max_attempts: int = 3

    def inspect_sheet(self) -> Mapping[str, Any] | None:
        response = execute_with_transient_retry(
            lambda: self.service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id,
                includeGridData=False,
                fields="sheets(properties(sheetId,title))",
            ).execute(),
            self.max_attempts,
        )
        for sheet in response.get("sheets", []):
            properties = sheet.get("properties", {})
            if properties.get("title") == self.sheet_name:
                self.worksheet_id = properties.get("sheetId")
                return properties
        return None

    def read_values(self) -> list[list[Any]]:
        response = execute_with_transient_retry(
            lambda: self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id, range=f"'{self.sheet_name}'!A:ZZ"
            ).execute(), self.max_attempts,
        )
        return response.get("values", [])

    def write_cells(self, start_row: int, start_column: int, values: list[list[Any]]) -> None:
        if not values:
            return
        end_row = start_row + len(values) - 1
        end_column = start_column + max(len(row) for row in values) - 1
        cell_range = f"'{self.sheet_name}'!{_column_name(start_column)}{start_row}:{_column_name(end_column)}{end_row}"
        execute_with_transient_retry(
            lambda: self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id, range=cell_range,
                valueInputOption="RAW", body={"values": values},
            ).execute(), self.max_attempts,
        )

    def apply_changes(self, updates, inserts, deletes) -> None:
        if updates:
            data = []
            for row_number, fields in sorted(updates.items()):
                for start, values in _contiguous_updates(fields):
                    data.append({
                        "range": f"'{self.sheet_name}'!{_column_name(start + 1)}{row_number}:{_column_name(start + len(values))}{row_number}",
                        "values": [values],
                    })
            execute_with_transient_retry(
                lambda: self.service.spreadsheets().values().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"valueInputOption": "RAW", "data": data},
                ).execute(), self.max_attempts,
            )
        if inserts:
            execute_with_transient_retry(
                lambda: self.service.spreadsheets().values().append(
                    spreadsheetId=self.spreadsheet_id, range=f"'{self.sheet_name}'!A:ZZ",
                    valueInputOption="RAW", insertDataOption="INSERT_ROWS",
                    body={"values": [list(row) for row in inserts]},
                ).execute(), self.max_attempts,
            )
        if deletes:
            worksheet_id = self.worksheet_id
            if worksheet_id is None:
                raise RuntimeError("worksheet_id requerido para eliminar filas")
            requests = [{"deleteDimension": {"range": {
                "sheetId": worksheet_id, "dimension": "ROWS",
                "startIndex": row_number - 1, "endIndex": row_number,
            }}} for row_number in sorted(deletes, reverse=True)]
            execute_with_transient_retry(
                lambda: self.service.spreadsheets().batchUpdate(
                    spreadsheetId=self.spreadsheet_id, body={"requests": requests}
                ).execute(), self.max_attempts,
            )

    def reorder_rows(self, participant_ids: Sequence[str]) -> None:
        if not participant_ids:
            return
        values = self.read_values()
        positions = {_key(value): index for index, value in enumerate(values[0])} if values else {}
        participant_column = positions.get("participant_id")
        if participant_column is None or self.worksheet_id is None:
            raise RuntimeError("Ranking requiere participant_id y worksheet_id")
        current = {
            str(row[participant_column]).strip(): row_number
            for row_number, row in enumerate(values[1:], 2)
            if participant_column < len(row) and str(row[participant_column]).strip()
        }
        requests = []
        for target_row, participant_id in enumerate(participant_ids, 2):
            current_row = current.get(participant_id)
            if current_row is None or current_row == target_row:
                continue
            requests.append({"moveDimension": {"source": {
                "sheetId": self.worksheet_id, "dimension": "ROWS",
                "startIndex": current_row - 1, "endIndex": current_row,
            }, "destinationIndex": target_row - 1}})
            for identifier, row_number in list(current.items()):
                if identifier == participant_id:
                    current[identifier] = target_row
                elif target_row <= row_number < current_row:
                    current[identifier] = row_number + 1
                elif current_row < row_number <= target_row:
                    current[identifier] = row_number - 1
        if requests:
            execute_with_transient_retry(
                lambda: self.service.spreadsheets().batchUpdate(
                    spreadsheetId=self.spreadsheet_id, body={"requests": requests}
                ).execute(), self.max_attempts,
            )


class RankingRepository:
    """Persist the complete derived Ranking view without upstream processing."""

    def __init__(self, gateway: RankingGateway):
        self.gateway = gateway
        self.headers: list[str] = []

    def migration_plan(self) -> dict[str, Any]:
        sheet = self.gateway.inspect_sheet()
        if sheet is None:
            return {"action": "CREATE_SHEET", "sheet_name": "Ranking"}
        values = self.gateway.read_values()
        headers = [str(value).strip() for value in (values[0] if values else [])]
        missing = [header for header in CANONICAL_RANKING_HEADERS if _key(header) not in {_key(item) for item in headers}]
        return {"action": "ADD_HEADERS" if missing else "NOOP", "sheet_name": "Ranking", "missing": missing}

    def migrate(self) -> list[list[Any]]:
        if self.gateway.inspect_sheet() is None:
            raise ValueError("CREATE_SHEET requerido para Ranking")
        values = self.gateway.read_values()
        if not values:
            self.gateway.write_cells(1, 1, [CANONICAL_RANKING_HEADERS])
            values = self.gateway.read_values()
            self._verify_headers(values, CANONICAL_RANKING_HEADERS)
            self.headers = list(CANONICAL_RANKING_HEADERS)
            return values
        headers = [str(value).strip() for value in values[0]]
        positions = self._positions(headers)
        missing = [header for header in CANONICAL_RANKING_HEADERS if _key(header) not in positions]
        has_data = any(any(str(value).strip() for value in row) for row in values[1:])
        if has_data and "participant_id" not in positions:
            raise ValueError("NEEDS_MIGRATION: filas existentes sin participant_id")
        self.headers = headers + missing
        if missing:
            self.gateway.write_cells(1, len(headers) + 1, [missing])
            values = self.gateway.read_values()
            self._verify_headers(values, self.headers)
        self._validate_existing(values[1:], self._positions(self.headers))
        return values

    def persist(self, ranking: ProgramRanking, participants: Iterable[Participant]) -> RankingWriteResult:
        values = self.migrate()
        positions = self._positions(self.headers)
        participant_map = _index_participants(participants)
        desired_rows = self._build_rows(ranking, participant_map, positions)
        existing_rows = [_pad(row, len(self.headers)) for row in values[1:]]
        self._validate_existing(existing_rows, positions)
        existing = {
            str(row[positions["participant_id"]]).strip(): (index + 2, row)
            for index, row in enumerate(existing_rows)
            if str(row[positions["participant_id"]]).strip()
        }
        desired = {str(row[positions["participant_id"]]).strip(): row for row in desired_rows}
        canonical_columns = [column for key, column in positions.items() if key in CANONICAL_RANKING_KEYS]
        updates = {
            row_number: {
                column: desired_row[column]
                for column in canonical_columns
                if not _cell_equal(existing_row[column], desired_row[column], _column_key(column, positions))
            }
            for participant_id, (row_number, existing_row) in existing.items()
            if participant_id in desired
            for desired_row in [desired[participant_id]]
            if any(not _cell_equal(existing_row[column], desired_row[column], _column_key(column, positions)) for column in canonical_columns)
        }
        deletes = [row_number for participant_id, (row_number, _) in existing.items() if participant_id not in desired]
        inserts = [row for participant_id, row in desired.items() if participant_id not in existing]
        desired_order = list(desired)
        existing_order = [participant_id for participant_id, _ in sorted(existing.items(), key=lambda item: item[1][0])]
        order_changed = existing_order != desired_order
        if not updates and not deletes and not inserts and not order_changed:
            return RankingWriteResult("NOOP", 0)
        self.gateway.apply_changes(updates, inserts, sorted(deletes, reverse=True))
        if order_changed or inserts or deletes:
            self.gateway.reorder_rows(desired_order)
        verified = self.gateway.read_values()
        self._verify_headers(verified, self.headers)
        self._verify_rows(verified[1:], desired, positions)
        return RankingWriteResult("REPLACE", len(desired_rows))

    def _build_rows(self, ranking, participant_map, positions):
        seen = set()
        for entry in ranking.entries:
            if not entry.participant_id:
                raise ValueError("participant_id es obligatorio")
            if entry.participant_id in seen:
                raise ValueError(f"participant_id duplicado: {entry.participant_id}")
            seen.add(entry.participant_id)
            participant = participant_map.get(entry.participant_id)
            if participant is None:
                raise ValueError(f"participant_id inexistente: {entry.participant_id}")
            if entry.role != "participant" or participant.role != "participant":
                raise ValueError(f"Ranking no puede contener role {entry.role}: {entry.participant_id}")
            if not isinstance(entry.rank, int) or isinstance(entry.rank, bool) or entry.rank < 1:
                raise ValueError(f"rank inválido: {entry.participant_id}")
            _decimal_score(entry.score_total)
        ordered = sorted(ranking.entries, key=lambda e: (e.rank, -e.score_total, participant_map[e.participant_id].nombre.casefold(), e.participant_id))
        rows = []
        for entry in ordered:
            participant = participant_map[entry.participant_id]
            values = [""] * len(self.headers)
            fields = {
                "rank": entry.rank, "participant_id": entry.participant_id,
                "participant": participant.nombre, "email": participant.correo,
                "sessions_with_activity": entry.sessions_with_activity,
                "voice_total": entry.voice_total, "voice_valid_total": entry.voice_valid_total,
                "chat_total": entry.chat_total, "chat_valid_total": entry.chat_valid_total,
                "score_total": _canonical_score(entry.score_total),
                "ranking_complete": entry.ranking_complete,
            }
            for field, value in fields.items():
                values[positions[field]] = value
            rows.append(values)
        return rows

    def _validate_existing(self, rows, positions):
        seen = set()
        for row_number, row in enumerate(rows, 2):
            padded = _pad(row, len(self.headers))
            if not any(str(value).strip() for value in padded):
                continue
            participant_id = str(padded[positions["participant_id"]]).strip()
            if not participant_id:
                raise ValueError(f"NEEDS_MIGRATION: fila {row_number} sin participant_id")
            if participant_id in seen:
                raise ValueError(f"Resultado duplicado en fila {row_number}: {participant_id}")
            seen.add(participant_id)

    def _positions(self, headers):
        positions = {_key(header): index for index, header in enumerate(headers)}
        if len(positions) != len(headers):
            raise ValueError("La hoja Ranking contiene headers duplicados")
        return positions

    def _verify_headers(self, values, expected):
        if not values or list(values[0]) != list(expected):
            raise RuntimeError("No se pudo verificar el esquema de Ranking")

    def _verify_rows(self, rows, desired, positions):
        actual = {}
        actual_order = []
        for row in rows:
            padded = _pad(row, len(self.headers))
            participant_id = str(padded[positions["participant_id"]]).strip()
            if participant_id:
                actual[participant_id] = padded
                actual_order.append(participant_id)
        if set(actual) != set(desired):
            raise RuntimeError("No se pudo verificar el conjunto de Ranking")
        if actual_order != list(desired):
            raise RuntimeError("No se pudo verificar el orden de Ranking")
        canonical_columns = [column for key, column in positions.items() if key in CANONICAL_RANKING_KEYS]
        for participant_id, expected in desired.items():
            if any(not _cell_equal(actual[participant_id][column], expected[column], _column_key(column, positions)) for column in canonical_columns):
                raise RuntimeError(f"No se pudo verificar el ranking de {participant_id}")



def _column_key(column, positions):
    return next(key for key, index in positions.items() if index == column)


def _pad(row, length):
    return list(row) + [""] * max(0, length - len(row))


def _index_participants(participants):
    indexed = {}
    for participant in participants:
        if participant.participant_id in indexed:
            raise ValueError(f"participant_id duplicado: {participant.participant_id}")
        indexed[participant.participant_id] = participant
    return indexed


def _decimal_score(value):
    try:
        score = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Score inválido: {value}") from exc
    if score < 0:
        raise ValueError(f"Score inválido: {value}")
    return score


def _canonical_score(value):
    return format(_decimal_score(value), "f")


def _cell_equal(left, right, field):
    if field == "ranking_complete":
        if isinstance(left, bool) and isinstance(right, str):
            return right.strip().casefold() == str(left).casefold()
        if isinstance(right, bool) and isinstance(left, str):
            return left.strip().casefold() == str(right).casefold()
        return left == right
    if field in NUMERIC_RANKING_KEYS:
        if isinstance(left, bool) or isinstance(right, bool):
            return left == right
        try:
            return Decimal(str(left).strip()) == Decimal(str(right).strip())
        except (InvalidOperation, ValueError):
            return left == right
    return left == right


def _contiguous_updates(fields):
    ordered = sorted(fields.items())
    groups = []
    for column, value in ordered:
        if not groups or column != groups[-1][0] + len(groups[-1][1]):
            groups.append((column, [value]))
        else:
            groups[-1][1].append(value)
    return groups


def _column_name(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result
