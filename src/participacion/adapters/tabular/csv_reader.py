from __future__ import annotations

import csv
from pathlib import Path
from ...application.external_data.models import TabularRow, TabularTable


class TabularReadError(ValueError):
    """The physical tabular source cannot be interpreted structurally."""


def read_csv_table(path: str | Path, *, delimiter: str = ",", encoding: str = "utf-8-sig") -> TabularTable:
    source = Path(path).expanduser()
    with source.open("r", encoding=encoding, newline="") as handle:
        rows = list(csv.reader(handle, delimiter=delimiter))
    if not rows:
        raise TabularReadError("El CSV está vacío")
    columns = _headers(rows[0])
    return TabularTable(
        source.name,
        None,
        columns,
        tuple(
            TabularRow(number, dict(zip(columns, values)))
            for number, values in enumerate(rows[1:], 2)
            if any(str(value).strip() for value in values)
        ),
    )


def _headers(row: list[str]) -> tuple[str, ...]:
    columns = tuple(value.strip() or f"__unnamed_{index}" for index, value in enumerate(row, 1))
    if len(set(columns)) != len(columns):
        raise TabularReadError("El CSV contiene encabezados duplicados")
    return columns
