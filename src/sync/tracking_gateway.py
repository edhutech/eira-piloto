from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .participant_repository import execute_with_transient_retry
from .tracking_repository import TrackingWriteResult


@dataclass
class GoogleSheetsTrackingGateway:
    service: Any
    spreadsheet_id: str
    sheet_name: str = "Seguimiento"
    max_attempts: int = 3

    def ensure_sheet(self, session_count: int) -> tuple[bool, int | None]:
        metadata = execute_with_transient_retry(
            lambda: self.service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id,
                includeGridData=False,
                fields="sheets(properties(title,sheetId,index))",
            ).execute(), self.max_attempts)
        for item in metadata.get("sheets", []):
            props = item.get("properties", {})
            if props.get("title") == self.sheet_name:
                if props.get("index", 0) != 0:
                    execute_with_transient_retry(
                        lambda: self.service.spreadsheets().batchUpdate(
                            spreadsheetId=self.spreadsheet_id,
                            body={"requests": [{"updateSheetProperties": {
                                "properties": {"sheetId": props.get("sheetId"), "index": 0},
                                "fields": "index",
                            }}]},
                        ).execute(), self.max_attempts)
                return False, props.get("sheetId")
        response = execute_with_transient_retry(
            lambda: self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {
                    "title": self.sheet_name, "index": 0,
                }}}]},
            ).execute(), self.max_attempts)
        replies = response.get("replies", [])
        properties = replies[0].get("addSheet", {}).get("properties", {}) if replies else {}
        return True, properties.get("sheetId")

    def read_values(self) -> list[list[Any]]:
        response = execute_with_transient_retry(
            lambda: self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id, range=f"'{self.sheet_name}'!A:ZZ"
            ).execute(), self.max_attempts)
        return response.get("values", [])

    def persist(self, values: list[list[Any]], manual_cells: set[tuple[int, int]],
                structural_change: bool, worksheet_id: int | None) -> TrackingWriteResult:
        existing = self.read_values()
        if structural_change:
            self._write_values(_json_values(values))
            self._format(values, worksheet_id)
            return TrackingWriteResult("REPLACE", max(0, len(values) - 5))
        updates = []
        for row_index, row in enumerate(values):
            old = list(existing[row_index]) if row_index < len(existing) else []
            for column_index, value in enumerate(row):
                if (row_index, column_index) in manual_cells:
                    continue
                old_value = old[column_index] if column_index < len(old) else ""
                if row_index == 3 and column_index >= 4 and (column_index - 4) % 3 == 0:
                    continue
                if _same_value(old_value, value):
                    continue
                if old_value != value:
                    updates.append({"range": f"'{self.sheet_name}'!{_column(column_index + 1)}{row_index + 1}", "values": [[value]]})
        if updates:
            execute_with_transient_retry(
                lambda: self.service.spreadsheets().values().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"valueInputOption": "USER_ENTERED", "data": updates},
                ).execute(), self.max_attempts)
            return TrackingWriteResult("REPLACE", len(values) - 5)
        return TrackingWriteResult("NOOP", len(values) - 5)

    def _write_values(self, values: list[list[Any]]) -> None:
        execute_with_transient_retry(
            lambda: self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id, range=f"'{self.sheet_name}'!A1",
                valueInputOption="USER_ENTERED", body={"values": values},
            ).execute(), self.max_attempts)

    def _format(self, values: Sequence[Sequence[Any]], worksheet_id: int | None) -> None:
        if worksheet_id is None:
            return
        requests: list[dict[str, Any]] = [
            {"mergeCells": {"range": {"sheetId": worksheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 3}, "mergeType": "MERGE_ALL"}},
            {"updateSheetProperties": {"properties": {"sheetId": worksheet_id,
             "gridProperties": {"frozenRowCount": 5, "frozenColumnCount": 3}}, "fields": "gridProperties(frozenRowCount,frozenColumnCount)"}},
            {"updateDimensionProperties": {"range": {"sheetId": worksheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1}, "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
            {"updateDimensionProperties": {"range": {"sheetId": worksheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 3}, "properties": {"pixelSize": 150}, "fields": "pixelSize"}},
            {"repeatCell": {"range": {"sheetId": worksheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(values[0])}, "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.36}, "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True}}}, "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
            {"repeatCell": {"range": {"sheetId": worksheet_id, "startRowIndex": 4, "endRowIndex": 5, "startColumnIndex": 0, "endColumnIndex": len(values[0])}, "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.75, "green": 0.85, "blue": 0.95}, "textFormat": {"bold": True}}}, "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
        ]
        for index in range((len(values[0]) - 3) // 3):
            start = 3 + index * 3
            requests.append({"mergeCells": {"range": {"sheetId": worksheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": start, "endColumnIndex": start + 3}, "mergeType": "MERGE_ALL"}})
            requests.append({"repeatCell": {"range": {"sheetId": worksheet_id, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": start + 1, "endColumnIndex": start + 2}, "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1, "green": 0.94, "blue": 0.72}, "numberFormat": {"type": "NUMBER", "pattern": "0"}}}, "fields": "userEnteredFormat(backgroundColor,numberFormat)"}})
            requests.append({"repeatCell": {"range": {"sheetId": worksheet_id, "startRowIndex": 3, "endRowIndex": 4, "startColumnIndex": start + 1, "endColumnIndex": start + 2}, "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}}, "fields": "userEnteredFormat(numberFormat)"}})
            requests.append({"setDataValidation": {"range": {"sheetId": worksheet_id, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": start + 1, "endColumnIndex": start + 2}, "rule": {"condition": {"type": "NUMBER_GREATER_THAN_EQ", "values": [{"userEnteredValue": "0"}]}, "strict": True, "showCustomUi": True}}})
            requests.append({"repeatCell": {"range": {"sheetId": worksheet_id, "startRowIndex": 5, "endRowIndex": len(values), "startColumnIndex": start + 2, "endColumnIndex": start + 3}, "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.93}, "numberFormat": {"type": "NUMBER", "pattern": "0.0"}}}, "fields": "userEnteredFormat(backgroundColor,numberFormat)"}})
            warning_formula = f'=AND({_column(start + 2)}2<>"",{_column(start + 2)}2>{_column(start + 1)}2)'
            requests.append({"addConditionalFormatRule": {
                "rule": {
                    "ranges": [{"sheetId": worksheet_id, "startRowIndex": 1, "endRowIndex": 2,
                                "startColumnIndex": start + 1, "endColumnIndex": start + 2}],
                    "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": warning_formula}]},
                        "format": {"backgroundColor": {"red": 1, "green": 0.8, "blue": 0.8}},
                    },
                },
                "index": 0,
            }})
        execute_with_transient_retry(
            lambda: self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id, body={"requests": requests}
            ).execute(), self.max_attempts)


def _column(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _json_values(values: Sequence[Sequence[Any]]) -> list[list[Any]]:
    from decimal import Decimal
    return [[str(value) if isinstance(value, Decimal) else value for value in row] for row in values]


def _same_value(left: Any, right: Any) -> bool:
    from decimal import Decimal, InvalidOperation
    if isinstance(right, Decimal) and not isinstance(left, Decimal):
        try:
            return Decimal(str(left)) == right
        except (InvalidOperation, ValueError):
            return False
    return left == right
