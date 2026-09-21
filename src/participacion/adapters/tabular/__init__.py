"""Readers for external structured sources."""

from .csv_reader import read_csv_table
from .google_sheets_reader import read_google_sheet_table
from .xlsx_reader import read_xlsx_table

__all__ = ["read_csv_table", "read_google_sheet_table", "read_xlsx_table"]
