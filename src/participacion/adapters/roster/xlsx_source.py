from __future__ import annotations

from pathlib import Path

from ...application.roster import RosterRecord, roster_records_from_rows


def read_xlsx_roster(path: str | Path) -> list[RosterRecord]:
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("XLSX requiere openpyxl instalado; usa participacion-agent[xlsx]") from exc
    workbook = openpyxl.load_workbook(Path(path).expanduser(), read_only=True, data_only=True)
    try:
        sheet = workbook.active
        return roster_records_from_rows(
            [] if sheet is None else [list(row) for row in sheet.iter_rows(values_only=True)]
        )
    finally:
        workbook.close()
