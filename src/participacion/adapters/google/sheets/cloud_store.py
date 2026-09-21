from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from ....application.ports.contracts import StateStore
from ...filesystem.state import empty_state
from ..retry import execute_with_transient_retry
from .schema import CLOUD_JSON_HEADERS


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class GoogleSheetsJsonStore:
    """Small key/value JSON store backed by a dedicated Google Sheet tab."""

    def __init__(self, service: Any, spreadsheet_id: str, sheet_name: str,
                 max_attempts: int = 3):
        self.service = service
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        self.max_attempts = max_attempts

    def _execute(self, operation: Any) -> Any:
        return execute_with_transient_retry(operation, self.max_attempts)

    def read_values(self) -> list[list[Any]]:
        response = self._execute(lambda: self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{self.sheet_name}'!A:C",
        ).execute())
        return response.get("values", [])

    def ensure(self) -> None:
        values = self.read_values()
        if not values:
            self._execute(lambda: self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{self.sheet_name}'!A1:C1",
                valueInputOption="RAW",
                body={"values": [CLOUD_JSON_HEADERS]},
            ).execute())
            return
        headers = [str(value).strip() for value in values[0]]
        if headers != CLOUD_JSON_HEADERS:
            raise ValueError(
                f"{self.sheet_name} requiere encabezados exactos: "
                + ", ".join(CLOUD_JSON_HEADERS)
            )

    def load_all(self) -> dict[str, Any]:
        self.ensure()
        values = self.read_values()
        result: dict[str, Any] = {}
        for row_number, row in enumerate(values[1:], 2):
            key = str(row[0] if row else "").strip()
            if not key:
                continue
            if key in result:
                raise ValueError(f"{self.sheet_name} contiene clave duplicada: {key}")
            raw = str(row[1] if len(row) > 1 else "").strip()
            if not raw:
                raise ValueError(f"{self.sheet_name} fila {row_number}: value_json vacío")
            try:
                result[key] = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{self.sheet_name} fila {row_number}: JSON inválido para {key}"
                ) from exc
        return result

    def get(self, key: str, default: Any = None) -> Any:
        return self.load_all().get(key, default)

    def put(self, key: str, value: Any) -> None:
        normalized = str(key).strip()
        if not normalized:
            raise ValueError("La clave cloud no puede estar vacía")
        self.ensure()
        values = self.read_values()
        rows: dict[str, int] = {}
        for row_number, row in enumerate(values[1:], 2):
            existing = str(row[0] if row else "").strip()
            if existing:
                if existing in rows:
                    raise ValueError(f"{self.sheet_name} contiene clave duplicada: {existing}")
                rows[existing] = row_number
        payload = [[normalized, json.dumps(value, ensure_ascii=False, sort_keys=True), _utc_now()]]
        row_number = rows.get(normalized)
        if row_number is None:
            self._execute(lambda: self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{self.sheet_name}'!A:C",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": payload},
            ).execute())
        else:
            self._execute(lambda: self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{self.sheet_name}'!A{row_number}:C{row_number}",
                valueInputOption="RAW",
                body={"values": payload},
            ).execute())


class GoogleSheetsStateStore(StateStore):
    """Authoritative sync state stored in the Eira workbook."""

    def __init__(self, store: GoogleSheetsJsonStore, key: str = "sync_state"):
        self.store = store
        self.key = key

    def load(self) -> dict[str, Any]:
        value = self.store.get(self.key)
        if value is None:
            return empty_state()
        if not isinstance(value, Mapping) or value.get("version") != 1:
            raise ValueError("Estado cloud tiene un formato o versión inválidos")
        result = dict(value)
        programs = result.setdefault("programs", {})
        if not isinstance(programs, dict):
            raise ValueError("Estado cloud programs debe ser un objeto")
        return result

    def save(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError("Estado cloud tiene un formato o versión inválidos")
        self.store.put(self.key, state)
