from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Protocol, Sequence

from .participant_repository import execute_with_transient_retry
from .scoring import ParticipantSessionScore


CANONICAL_SESSION_HEADERS = [
    "session_number", "session_name", "participant_id", "participant", "email",
    "voice_total", "voice_valid", "chat_total", "chat_valid", "ambiguous_total",
    "score", "scoring_complete", "countability_ruleset_version",
]
CANONICAL_SESSION_KEYS = {str(header).strip().casefold() for header in CANONICAL_SESSION_HEADERS}
NUMERIC_SESSION_KEYS = {
    "session_number", "voice_total", "voice_valid", "chat_total", "chat_valid",
    "ambiguous_total", "score", "countability_ruleset_version",
}


class SessionResultsGateway(Protocol):
    def read_values(self) -> list[list[Any]]: ...
    def write_cells(self, start_row: int, start_column: int, values: list[list[Any]]) -> None: ...
    def apply_changes(
        self,
        updates: Mapping[int, Mapping[int, Any]],
        inserts: Sequence[Sequence[Any]],
        deletes: Sequence[int],
    ) -> None: ...


@dataclass
class GoogleSheetsSessionResultsGateway:
    """Google Sheets adapter using bounded batch value and row operations."""

    service: Any
    spreadsheet_id: str
    worksheet_id: int
    sheet_name: str = "Sesiones"
    max_attempts: int = 3

    def read_values(self) -> list[list[Any]]:
        response = execute_with_transient_retry(
            lambda: self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{self.sheet_name}'!A:ZZ",
            ).execute(),
            self.max_attempts,
        )
        return response.get("values", [])

    def write_cells(self, start_row: int, start_column: int, values: list[list[Any]]) -> None:
        if not values:
            return
        end_row = start_row + len(values) - 1
        end_column = start_column + max(len(row) for row in values) - 1
        cell_range = (
            f"'{self.sheet_name}'!{_column_name(start_column)}{start_row}:"
            f"{_column_name(end_column)}{end_row}"
        )
        execute_with_transient_retry(
            lambda: self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=cell_range,
                valueInputOption="RAW",
                body={"values": values},
            ).execute(),
            self.max_attempts,
        )

    def apply_changes(
        self,
        updates: Mapping[int, Mapping[int, Any]],
        inserts: Sequence[Sequence[Any]],
        deletes: Sequence[int],
    ) -> None:
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
                ).execute(),
                self.max_attempts,
            )
        if inserts:
            execute_with_transient_retry(
                lambda: self.service.spreadsheets().values().append(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{self.sheet_name}'!A:ZZ",
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [list(row) for row in inserts]},
                ).execute(),
                self.max_attempts,
            )
        if deletes:
            requests = [{
                "deleteDimension": {
                    "range": {
                        "sheetId": self.worksheet_id,
                        "dimension": "ROWS",
                        "startIndex": row_number - 1,
                        "endIndex": row_number,
                    }
                }
            } for row_number in sorted(deletes, reverse=True)]
            execute_with_transient_retry(
                lambda: self.service.spreadsheets().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"requests": requests},
                ).execute(),
                self.max_attempts,
            )


def _contiguous_updates(fields: Mapping[int, Any]) -> list[tuple[int, list[Any]]]:
    ordered = sorted(fields.items())
    groups: list[tuple[int, list[Any]]] = []
    for column, value in ordered:
        if not groups or column != groups[-1][0] + len(groups[-1][1]):
            groups.append((column, [value]))
        else:
            groups[-1][1].append(value)
    return groups


def _column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


@dataclass(frozen=True)
class ParticipantSnapshot:
    participant_id: str
    participant: str
    email: str = ""


@dataclass(frozen=True)
class SessionWriteResult:
    status: str
    session_number: int
    rows_written: int


class SessionResultsRepository:
    """Persist complete session results through a batch-oriented gateway."""

    def __init__(self, gateway: SessionResultsGateway):
        self.gateway = gateway
        self.headers: list[str] = []

    def migrate(self) -> list[list[Any]]:
        values = self.gateway.read_values()
        if not values:
            self.gateway.write_cells(1, 1, [CANONICAL_SESSION_HEADERS])
            values = self.gateway.read_values()
            self._verify_headers(values, CANONICAL_SESSION_HEADERS)
            self.headers = list(CANONICAL_SESSION_HEADERS)
            return values

        headers = [str(value).strip() for value in values[0]]
        positions = self._header_positions(headers)
        missing = [header for header in CANONICAL_SESSION_HEADERS if _header_key(header) not in positions]
        has_data = any(any(str(value).strip() for value in row) for row in values[1:])
        if has_data and "participant_id" not in positions:
            raise ValueError("NEEDS_MIGRATION: filas existentes sin participant_id")

        working_headers = headers + missing
        self.headers = working_headers
        if missing:
            self.gateway.write_cells(1, len(headers) + 1, [missing])
            values = self.gateway.read_values()
            self._verify_headers(values, working_headers)
        self._validate_existing_rows(
            [_pad(row, len(self.headers)) for row in values[1:]],
            self._positions(),
        )
        return values

    def load_records(self) -> list[dict[str, Any]]:
        values = self.migrate()
        positions = self._positions()
        records = []
        for row_number, row in enumerate(values[1:], 2):
            padded = _pad(row, len(self.headers))
            if not any(str(value).strip() for value in padded):
                continue
            record = {header: padded[index] for header, index in positions.items()}
            record["_row_number"] = row_number
            records.append(record)
        return records

    def replace_session(
        self,
        session_number: int,
        session_name: str,
        scores: Sequence[ParticipantSessionScore],
        participant_snapshots: Mapping[str, ParticipantSnapshot],
        *,
        ruleset_version: int = 1,
    ) -> SessionWriteResult:
        values = self.migrate()
        positions = self._positions()
        existing_rows = [_pad(row, len(self.headers)) for row in values[1:]]
        self._validate_existing_rows(existing_rows, positions)
        desired_session_rows = self._build_session_rows(
            session_number, session_name, scores, participant_snapshots, positions, ruleset_version
        )
        existing_target = {
            str(row[positions["participant_id"]]).strip(): (index + 2, row)
            for index, row in enumerate(existing_rows)
            if _session_value(row, positions) == session_number
        }
        desired_by_id = {
            str(row[positions["participant_id"]]).strip(): row
            for row in desired_session_rows
        }
        canonical_columns = [column for key, column in positions.items() if key in CANONICAL_SESSION_KEYS]
        updates = {
            row_number: {
                column: desired_row[column]
                for column in canonical_columns
                if not _cell_equal(existing_row[column], desired_row[column], _column_key(column, positions))
            }
            for participant_id, (row_number, existing_row) in existing_target.items()
            if participant_id in desired_by_id
            for desired_row in [desired_by_id[participant_id]]
            if any(not _cell_equal(existing_row[column], desired_row[column], _column_key(column, positions)) for column in canonical_columns)
        }
        deletes = [row_number for participant_id, (row_number, _) in existing_target.items()
                   if participant_id not in desired_by_id]
        inserts = [row for participant_id, row in desired_by_id.items() if participant_id not in existing_target]

        if not updates and not deletes and not inserts:
            return SessionWriteResult("NOOP", session_number, 0)

        self.gateway.apply_changes(updates, inserts, sorted(deletes, reverse=True))
        verified_values = self.gateway.read_values()
        self._verify_headers(verified_values, self.headers)
        verified_rows = [_pad(row, len(self.headers)) for row in verified_values[1:]]
        self._verify_session_rows(verified_rows, session_number, desired_by_id, positions)
        return SessionWriteResult("REPLACE", session_number, len(desired_session_rows))

    def _verify_session_rows(
        self,
        rows: Sequence[Sequence[Any]],
        session_number: int,
        desired_by_id: Mapping[str, Sequence[Any]],
        positions: Mapping[str, int],
    ) -> None:
        actual = {
            str(row[positions["participant_id"]]).strip(): row
            for row in rows
            if _session_value(row, positions) == session_number
        }
        if set(actual) != set(desired_by_id):
            raise RuntimeError(f"No se pudo verificar el reemplazo de la sesión {session_number}")
        canonical_columns = [column for key, column in positions.items() if key in CANONICAL_SESSION_KEYS]
        for participant_id, expected in desired_by_id.items():
            if any(not _cell_equal(actual[participant_id][column], expected[column], _column_key(column, positions)) for column in canonical_columns):
                raise RuntimeError(f"No se pudo verificar el resultado de {participant_id}")

    def _build_session_rows(
        self,
        session_number: int,
        session_name: str,
        scores: Sequence[ParticipantSessionScore],
        snapshots: Mapping[str, ParticipantSnapshot],
        positions: Mapping[str, int],
        ruleset_version: int,
    ) -> list[list[Any]]:
        rows: list[list[Any]] = []
        seen: set[str] = set()
        for result in scores:
            if result.session_number != session_number:
                raise ValueError("El ParticipantSessionScore no corresponde a la sesión solicitada")
            participant_id = str(result.participant_id).strip()
            if not participant_id:
                raise ValueError("participant_id es obligatorio")
            if participant_id in seen:
                raise ValueError(f"participant_id duplicado: {participant_id}")
            seen.add(participant_id)
            snapshot = snapshots.get(participant_id)
            if snapshot is None:
                raise ValueError(f"Falta snapshot del participante: {participant_id}")
            if snapshot.participant_id != participant_id or not snapshot.participant.strip():
                raise ValueError(f"Snapshot inválido para participant_id: {participant_id}")
            score = _canonical_score(result.score)
            row = [""] * len(self.headers)
            fields = {
                "session_number": session_number,
                "session_name": session_name,
                "participant_id": participant_id,
                "participant": snapshot.participant,
                "email": snapshot.email,
                "voice_total": result.voice_total,
                "voice_valid": result.voice_valid,
                "chat_total": result.chat_total,
                "chat_valid": result.chat_valid,
                "ambiguous_total": result.ambiguous_total,
                "score": score,
                "scoring_complete": result.scoring_complete,
                "countability_ruleset_version": ruleset_version,
            }
            for field, value in fields.items():
                row[positions[_header_key(field)]] = value
            rows.append(row)
        return rows

    def _validate_existing_rows(self, rows: Sequence[list[Any]], positions: Mapping[str, int]) -> None:
        keys: set[tuple[int, str]] = set()
        for row_number, row in enumerate(rows, 2):
            if not any(str(value).strip() for value in row):
                continue
            participant_id = str(row[positions["participant_id"]]).strip()
            if not participant_id:
                raise ValueError(f"NEEDS_MIGRATION: fila {row_number} sin participant_id")
            try:
                session_number = int(row[positions["session_number"]])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Fila {row_number} tiene session_number inválido") from exc
            key = (session_number, participant_id)
            if key in keys:
                raise ValueError(f"Resultado duplicado en la fila {row_number}: {key}")
            keys.add(key)

    def _positions(self) -> dict[str, int]:
        return self._header_positions(self.headers)

    def _header_positions(self, headers: Sequence[Any]) -> dict[str, int]:
        keys = [_header_key(header) for header in headers]
        if len(keys) != len(set(keys)):
            raise ValueError("La hoja Sesiones contiene encabezados duplicados")
        if not any(keys):
            raise ValueError("La hoja Sesiones no tiene encabezados válidos")
        return {key: index for index, key in enumerate(keys)}

    def _verify_headers(self, values: Sequence[Sequence[Any]], expected: Sequence[str]) -> None:
        if not values or list(values[0]) != list(expected):
            raise RuntimeError("No se pudo verificar el esquema de Sesiones")


def _header_key(value: Any) -> str:
    return str(value).strip().casefold()


def _column_key(column: int, positions: Mapping[str, int]) -> str:
    return next(key for key, index in positions.items() if index == column)


def _pad(row: Sequence[Any], length: int) -> list[Any]:
    return list(row) + [""] * max(0, length - len(row))


def _session_value(row: Sequence[Any], positions: Mapping[str, int]) -> int:
    return int(row[positions["session_number"]])


def _canonical_score(value: Decimal | str | int | float) -> str:
    try:
        score = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Score inválido: {value}") from exc
    if score < 0 or (score * 2) != (score * 2).to_integral_value():
        raise ValueError("El score debe ser no negativo y estar en incrementos de 0.5")
    return format(score, "f")


def _cell_equal(left: Any, right: Any, field: str | None = None) -> bool:
    if field == "scoring_complete":
        if isinstance(left, bool) and isinstance(right, str):
            return right.strip().casefold() == str(left).casefold()
        if isinstance(right, bool) and isinstance(left, str):
            return left.strip().casefold() == str(right).casefold()
        return left == right
    if field in NUMERIC_SESSION_KEYS:
        if isinstance(left, bool) or isinstance(right, bool):
            return left == right
        try:
            return Decimal(str(left).strip()) == Decimal(str(right).strip())
        except (InvalidOperation, ValueError):
            return left == right
    return left == right
