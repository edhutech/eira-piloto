from __future__ import annotations

import unittest

from participacion.adapters.tabular.csv_reader import TabularReadError
from participacion.adapters.tabular.google_sheets_reader import read_google_sheet_table


class _Request:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _Values:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return _Request(self.payload)


class _Spreadsheets:
    def __init__(self, payload):
        self._values = _Values(payload)

    def values(self):
        return self._values


class _Service:
    def __init__(self, payload):
        self._spreadsheets = _Spreadsheets(payload)

    def spreadsheets(self):
        return self._spreadsheets


class GoogleSheetsTabularReaderTests(unittest.TestCase):
    def test_reads_live_sheet_into_tabular_table(self):
        service = _Service({"values": [
            ["email", "class_date", "attendance"],
            ["a@example.com", "2026-01-01", 1],
            ["", "", ""],
            ["b@example.com", "2026-01-02", 0],
        ]})

        table = read_google_sheet_table(
            service, "spreadsheet-id", sheet_name="participants"
        )

        self.assertEqual(table.source_name, "google_sheets:spreadsheet-id")
        self.assertEqual(table.sheet_name, "participants")
        self.assertEqual(table.columns, ("email", "class_date", "attendance"))
        self.assertEqual(len(table.rows), 2)
        self.assertEqual(table.rows[0].row_number, 2)
        self.assertEqual(table.rows[1].row_number, 4)
        self.assertEqual(table.rows[0].values["email"], "a@example.com")
        self.assertEqual(
            service._spreadsheets._values.calls[0]["range"],
            "'participants'!A:ZZ",
        )

    def test_rejects_empty_sheet(self):
        with self.assertRaisesRegex(TabularReadError, "vacía"):
            read_google_sheet_table(_Service({"values": []}), "sheet-id", sheet_name="data")

    def test_rejects_duplicate_headers(self):
        service = _Service({"values": [["email", "email"], ["a@example.com", "x"]]})
        with self.assertRaisesRegex(TabularReadError, "duplicados"):
            read_google_sheet_table(service, "sheet-id", sheet_name="data")

    def test_rejects_missing_source_or_tab(self):
        with self.assertRaisesRegex(TabularReadError, "spreadsheet_id"):
            read_google_sheet_table(_Service({"values": []}), "", sheet_name="data")
        with self.assertRaisesRegex(TabularReadError, "sheet_name"):
            read_google_sheet_table(_Service({"values": []}), "sheet-id", sheet_name="")


if __name__ == "__main__":
    unittest.main()
