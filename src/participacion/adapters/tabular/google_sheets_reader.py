from __future__ import annotations

from typing import Any

from ...application.external_data.models import TabularRow, TabularTable
from .csv_reader import TabularReadError


def read_google_sheet_table(
    sheets_service: Any,
    spreadsheet_id: str,
    *,
    sheet_name: str,
    range_name: str = "A:ZZ",
) -> TabularTable:
    """Read a live Google Sheet tab into the provider-neutral TabularTable model."""
    source_id = str(spreadsheet_id).strip()
    tab = str(sheet_name).strip()
    if not source_id:
        raise TabularReadError("Google Sheet requiere spreadsheet_id")
    if not tab:
        raise TabularReadError("Google Sheet requiere sheet_name")

    response = sheets_service.spreadsheets().values().get(
        spreadsheetId=source_id,
        range=f"'{tab}'!{range_name}",
    ).execute()
    raw_rows = response.get("values", [])
    if not raw_rows:
        raise TabularReadError(f"La hoja está vacía: {tab}")

    columns = _headers(tuple(raw_rows[0]))
    rows = tuple(
        TabularRow(
            row_number,
            dict(zip(columns, tuple(row) + (None,) * max(0, len(columns) - len(row)))),
        )
        for row_number, row in enumerate(raw_rows[1:], 2)
        if any(value is not None and str(value).strip() for value in row)
    )
    return TabularTable(f"google_sheets:{source_id}", tab, columns, rows)


def _headers(row: tuple[object, ...]) -> tuple[str, ...]:
    columns = tuple(
        str(value).strip() if value is not None and str(value).strip() else f"__unnamed_{index}"
        for index, value in enumerate(row, 1)
    )
    if len(set(columns)) != len(columns):
        raise TabularReadError("La hoja contiene encabezados duplicados")
    return columns
