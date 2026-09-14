from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Protocol

from .participants import Participant, strict_name_key

CANONICAL_PARTICIPANT_HEADERS = [
    "participant_id", "nombre", "correo", "aliases", "role", "source", "status",
]
PARTICIPANT_ROLES = {"participant", "facilitator", "other"}


def execute_with_transient_retry(operation: Any, max_attempts: int = 3) -> Any:
    for attempt in range(max_attempts):
        try:
            return operation()
        except Exception as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status != 429 and not (isinstance(status, int) and status >= 500):
                raise
            if attempt == max_attempts - 1:
                raise
            time.sleep(2 ** attempt)


def decode_aliases(cell: Any) -> list[str]:
    """Decode the canonical one-alias-per-line Sheets representation."""
    aliases: list[str] = []
    seen: set[str] = set()
    for value in str(cell or "").splitlines():
        alias = value.strip()
        key = strict_name_key(alias)
        if alias and key not in seen:
            aliases.append(alias)
            seen.add(key)
    return aliases


def encode_aliases(aliases: list[str] | None) -> str:
    """Encode aliases without using comma as a delimiter."""
    encoded: list[str] = []
    seen: set[str] = set()
    for value in aliases or []:
        alias = str(value).strip()
        key = strict_name_key(alias)
        if alias and key not in seen:
            encoded.append(alias)
            seen.add(key)
    return "\n".join(encoded)


class SheetsValuesGateway(Protocol):
    def read_values(self) -> list[list[Any]]: ...
    def write_cells(self, start_row: int, start_column: int, values: list[list[Any]]) -> None: ...
    def append_row(self, values: list[Any]) -> None: ...
    def append_rows(self, values: list[list[Any]]) -> None: ...


@dataclass
class GoogleSheetsValuesGateway:
    service: Any
    spreadsheet_id: str
    sheet_name: str = "Participantes"
    worksheet_id: int | None = None
    max_attempts: int = 3

    def read_values(self) -> list[list[Any]]:
        response = self._execute(lambda: self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{self.sheet_name}'!A:ZZ",
        ).execute())
        return response.get("values", [])

    def write_cells(self, start_row: int, start_column: int, values: list[list[Any]]) -> None:
        end_column = start_column + max((len(row) for row in values), default=1) - 1
        end_row = start_row + len(values) - 1
        cell_range = f"'{self.sheet_name}'!{_column_name(start_column)}{start_row}:{_column_name(end_column)}{end_row}"
        self._execute(lambda: self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=cell_range,
            valueInputOption="RAW",
            body={"values": values},
        ).execute())

    def append_row(self, values: list[Any]) -> None:
        self.append_rows([values])

    def append_rows(self, values: list[list[Any]]) -> None:
        self._execute(lambda: self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{self.sheet_name}'!A:ZZ",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute())

    def _execute(self, operation: Any) -> Any:
        return execute_with_transient_retry(operation, self.max_attempts)

    def ensure_role_validation(self) -> None:
        if self.worksheet_id is None:
            raise RuntimeError("worksheet_id requerido para validar role")
        self._execute(lambda: self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [{"setDataValidation": {
                "range": {"sheetId": self.worksheet_id, "startRowIndex": 1,
                          "startColumnIndex": 4, "endColumnIndex": 5},
                "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
                    {"userEnteredValue": "participant"},
                    {"userEnteredValue": "facilitator"},
                    {"userEnteredValue": "other"},
                ]}, "strict": True, "showCustomUi": True},
            }}]},
        ).execute())


class ParticipantRepository:
    """Persists participants through a Sheets values gateway."""

    def __init__(self, gateway: SheetsValuesGateway):
        self.gateway = gateway
        self.headers: list[str] = []

    def ensure_role_validation(self) -> None:
        method = getattr(self.gateway, "ensure_role_validation", None)
        if method is not None:
            method()

    def migrate(self) -> list[list[Any]]:
        values = self.gateway.read_values()
        if not values:
            self.gateway.write_cells(1, 1, [CANONICAL_PARTICIPANT_HEADERS])
            values = self.gateway.read_values()
            self._verify_header_values(values, CANONICAL_PARTICIPANT_HEADERS)
            self.headers = list(CANONICAL_PARTICIPANT_HEADERS)
            return values

        headers = [str(value).strip() for value in values[0]]
        if not any(headers):
            raise ValueError("La hoja Participantes no tiene encabezados válidos")
        header_keys = [strict_name_key(value) for value in headers]
        if len(header_keys) != len(set(header_keys)):
            raise ValueError("La hoja Participantes contiene encabezados duplicados")
        positions = {key: index for index, key in enumerate(header_keys)}
        missing = [name for name in CANONICAL_PARTICIPANT_HEADERS if strict_name_key(name) not in positions]
        working_headers = headers + missing
        working_positions = dict(positions)
        working_positions.update({strict_name_key(name): index for index, name in enumerate(missing, len(headers))})

        prepared: list[tuple[int, str, str]] = []
        identifiers: set[str] = set()
        for row_number, row in enumerate(values[1:], 2):
            padded = list(row) + [""] * (len(working_headers) - len(row))
            if not any(str(value).strip() for value in padded):
                continue
            name = str(padded[working_positions["nombre"]]).strip()
            if not name:
                raise ValueError(f"La fila {row_number} requiere nombre")
            participant_id = str(padded[working_positions["participant_id"]]).strip()
            if not participant_id:
                participant_id = _new_opaque_id()
                prepared.append((row_number, "participant_id", participant_id))
            if participant_id in identifiers:
                raise ValueError(f"participant_id duplicado en la fila {row_number}")
            identifiers.add(participant_id)
            role = str(padded[working_positions["role"]]).strip() or "participant"
            if role not in PARTICIPANT_ROLES:
                raise ValueError(f"Rol inválido en la fila {row_number}: {role}")
            if not str(padded[working_positions["role"]]).strip():
                prepared.append((row_number, "role", role))

        if missing:
            self.gateway.write_cells(1, len(headers) + 1, [missing])
        self.headers = working_headers
        for row_number, field, value in prepared:
            column = working_positions[strict_name_key(field)] + 1
            self.gateway.write_cells(row_number, column, [[value]])
        if missing or prepared:
            values = self.gateway.read_values()
            self._verify_migration_values(values, working_headers, prepared)
        return values

    def load_records(self) -> list[dict[str, Any]]:
        values = self.migrate()
        positions = self._positions()
        records = []
        for row in values[1:]:
            padded = list(row) + [""] * (len(self.headers) - len(row))
            if not any(str(value).strip() for value in padded):
                continue
            records.append({header: padded[index] for header, index in positions.items()})
        return records

    def load(self) -> list[Participant]:
        return [_record_to_participant(record) for record in self.load_records()]

    def upsert(self, participants: list[Participant]) -> None:
        records = self.load_records()
        existing_ids = {str(record["participant_id"]).strip() for record in records}
        positions = self._positions()
        seen = set()
        new_rows: list[list[Any]] = []
        for participant in participants:
            _validate_participant(participant)
            if participant.participant_id in seen:
                raise ValueError(f"participant_id duplicado en la entrada: {participant.participant_id}")
            seen.add(participant.participant_id)
            if participant.participant_id in existing_ids:
                continue
            row = [""] * len(self.headers)
            values = {
                "participant_id": participant.participant_id,
                "nombre": participant.nombre,
                "correo": participant.correo,
                "aliases": encode_aliases(participant.aliases),
                "role": participant.role,
                "source": participant.source,
                "status": participant.status,
            }
            for field, value in values.items():
                row[positions[field]] = value
            new_rows.append(row)
            existing_ids.add(participant.participant_id)
        if new_rows:
            self.gateway.append_rows(new_rows)
            verified = self.load_records()
            missing = {row[positions["participant_id"]] for row in new_rows} - {str(record["participant_id"]).strip() for record in verified}
            if missing:
                raise RuntimeError("No se pudieron verificar participantes: " + ", ".join(sorted(missing)))

    def _positions(self) -> dict[str, int]:
        return {strict_name_key(header): index for index, header in enumerate(self.headers)}

    def _verify_header_values(self, values: list[list[Any]], expected: list[str]) -> None:
        if not values or values[0][:len(expected)] != expected:
            raise RuntimeError("No se pudo verificar el encabezado de Participantes")

    def _verify_migration_values(self, values: list[list[Any]], headers: list[str], changes: list[tuple[int, str, str]]) -> None:
        if not values or values[0] != headers:
            raise RuntimeError("No se pudo verificar la migración de encabezados")
        positions = {strict_name_key(header): index for index, header in enumerate(headers)}
        for row_number, field, expected in changes:
            row = values[row_number - 1]
            if len(row) <= positions[field] or str(row[positions[field]]) != expected:
                raise RuntimeError(f"No se pudo verificar la migración de la fila {row_number}")


def _new_opaque_id() -> str:
    import uuid
    return f"participant_{uuid.uuid4().hex}"


def _record_to_participant(record: dict[str, Any]) -> Participant:
    name = str(record.get("nombre", "")).strip()
    if not name:
        raise ValueError("Un participante requiere nombre")
    participant_id = str(record.get("participant_id", "")).strip()
    if not participant_id:
        raise ValueError("Un participante persistido requiere participant_id")
    aliases = decode_aliases(record.get("aliases", ""))
    role = str(record.get("role", "participant")).strip() or "participant"
    if role not in PARTICIPANT_ROLES:
        raise ValueError(f"Rol de participante inválido: {role}")
    return Participant(participant_id, name, str(record.get("correo", "")).strip(), list(aliases), role, str(record.get("source", "")), str(record.get("status", "")))


def _validate_participant(participant: Participant) -> None:
    if not participant.participant_id.strip():
        raise ValueError("Un participante nuevo requiere participant_id")
    if not participant.nombre.strip():
        raise ValueError("Un participante requiere nombre")
    if participant.role not in PARTICIPANT_ROLES:
        raise ValueError(f"Rol de participante inválido: {participant.role}")


def _column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result
