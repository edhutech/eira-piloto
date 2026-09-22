from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..retry import execute_with_transient_retry
from .schema import STATIC_HEADERS


PALETTE = {
    "dark": {"red": 0.122, "green": 0.165, "blue": 0.267},
    "primary": {"red": 0.184, "green": 0.333, "blue": 0.592},
    "light": {"red": 0.851, "green": 0.906, "blue": 0.969},
    "very_light": {"red": 0.933, "green": 0.957, "blue": 0.984},
    "neutral": {"red": 0.953, "green": 0.957, "blue": 0.965},
    "border": {"red": 0.82, "green": 0.835, "blue": 0.863},
    "warning": {"red": 1.0, "green": 0.941, "blue": 0.722},
    "success": {"red": 0.851, "green": 0.941, "blue": 0.863},
    "error": {"red": 0.988, "green": 0.863, "blue": 0.863},
    "processing": {"red": 0.82, "green": 0.902, "blue": 0.973},
    "facilitator": {"red": 0.918, "green": 0.863, "blue": 0.961},
}

HEADERS = STATIC_HEADERS


@dataclass(frozen=True)
class StylingResult:
    requests: int
    sheets: tuple[str, ...]


class GoogleSheetStyler:
    """Idempotent visual system for all workbook tabs."""

    def __init__(self, service: Any, spreadsheet_id: str, max_attempts: int = 3):
        self.service = service
        self.spreadsheet_id = spreadsheet_id
        self.max_attempts = max_attempts

    def apply(self) -> StylingResult:
        metadata = execute_with_transient_retry(
            lambda: self.service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id,
                includeGridData=False,
                fields="sheets(properties(title,sheetId,index,gridProperties(rowCount,columnCount)),conditionalFormats,basicFilter)",
            ).execute(), self.max_attempts)
        requests: list[dict[str, Any]] = []
        names: list[str] = []
        for item in metadata.get("sheets", []):
            props = item.get("properties", {})
            name = str(props.get("title", ""))
            sheet_id = props.get("sheetId")
            if not name or sheet_id is None:
                continue
            names.append(name)
            requests.extend(self._sheet_requests(name, sheet_id, props, item))
        if requests:
            execute_with_transient_retry(
                lambda: self.service.spreadsheets().batchUpdate(
                    spreadsheetId=self.spreadsheet_id, body={"requests": requests}
                ).execute(), self.max_attempts)
        return StylingResult(len(requests), tuple(names))

    def _sheet_requests(self, name: str, sheet_id: int, props: Mapping[str, Any], metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
        if name in {"Configuración", "Estado"}:
            return [{"updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "hidden": True},
                "fields": "hidden",
            }}]
        if name not in HEADERS and name not in {"Seguimiento", "Seguimiento individual"}:
            return []
        if name == "Seguimiento":
            return self._tracking_requests(sheet_id, props)
        if name == "Seguimiento individual":
            return self._follow_up_requests(sheet_id, props, metadata)
        headers = HEADERS.get(name, ["campo", "valor"] if name == "Programa" else [])
        columns = len(headers)
        rows = max(int(props.get("gridProperties", {}).get("rowCount", 1000)), 2)
        requests = [
            {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {"hideGridlines": name in {"Programa", "Ranking"}, "frozenRowCount": 1, "frozenColumnCount": 0}}, "fields": "gridProperties(hideGridlines,frozenRowCount,frozenColumnCount)"}},
            {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1}, "properties": {"pixelSize": 30}, "fields": "pixelSize"}},
            {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": columns}, "cell": {"userEnteredFormat": {"backgroundColor": PALETTE["dark"], "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True, "fontSize": 10}, "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}}, "fields": "userEnteredFormat"}},
            {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": rows, "startColumnIndex": 0, "endColumnIndex": columns}, "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": "Arial", "fontSize": 10}, "verticalAlignment": "MIDDLE"}}, "fields": "userEnteredFormat.textFormat,userEnteredFormat.verticalAlignment"}},
            {"updateBorders": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": rows, "startColumnIndex": 0, "endColumnIndex": columns}, "top": {"style": "SOLID", "color": PALETTE["border"]}, "bottom": {"style": "SOLID", "color": PALETTE["border"]}, "innerHorizontal": {"style": "SOLID", "color": PALETTE["border"]}}},
        ]
        requests.extend(self._width_requests(name, sheet_id, columns))
        requests.extend(self._number_requests(name, sheet_id, rows, headers))
        requests.extend(self._conditional_requests(name, sheet_id, rows, headers, metadata))
        if name == "Participantes":
            requests.extend(self._participant_validations(sheet_id, rows, headers))
        if name == "Control" and "tracking_eligible" in headers:
            col = headers.index("tracking_eligible")
            requests.append({"setDataValidation": {"range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": rows, "startColumnIndex": col, "endColumnIndex": col + 1}, "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": "Sí"}, {"userEnteredValue": "No"}]}, "strict": True, "showCustomUi": True}}})
        if name != "Programa" and columns:
            if metadata.get("basicFilter"):
                requests.append({"clearBasicFilter": {"sheetId": sheet_id}})
            requests.append({"setBasicFilter": {"filter": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": rows, "startColumnIndex": 0, "endColumnIndex": columns}}}})
        return requests

    def _tracking_requests(self, sheet_id: int, props: Mapping[str, Any]) -> list[dict[str, Any]]:
        columns = max(int(props.get("gridProperties", {}).get("columnCount", 18)), 3)
        rows = max(int(props.get("gridProperties", {}).get("rowCount", 100)), 6)
        return [
            {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {"hideGridlines": True, "frozenRowCount": 5, "frozenColumnCount": 3}}, "fields": "gridProperties(hideGridlines,frozenRowCount,frozenColumnCount)"}},
            {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 5}, "properties": {"pixelSize": 26}, "fields": "pixelSize"}},
            {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2}, "properties": {"pixelSize": 180}, "fields": "pixelSize"}},
            {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": columns}, "cell": {"userEnteredFormat": {"backgroundColor": PALETTE["dark"], "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True}, "verticalAlignment": "MIDDLE"}}, "fields": "userEnteredFormat"}},
            {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 4, "endRowIndex": 5, "startColumnIndex": 0, "endColumnIndex": columns}, "cell": {"userEnteredFormat": {"backgroundColor": PALETTE["light"], "textFormat": {"bold": True}, "horizontalAlignment": "CENTER"}}, "fields": "userEnteredFormat"}},
            {"updateBorders": {"range": {"sheetId": sheet_id, "startRowIndex": 4, "endRowIndex": rows, "startColumnIndex": 3, "endColumnIndex": columns}, "top": {"style": "SOLID", "color": PALETTE["border"]}, "bottom": {"style": "SOLID", "color": PALETTE["border"]}, "innerHorizontal": {"style": "SOLID", "color": PALETTE["border"]}, "innerVertical": {"style": "SOLID", "color": PALETTE["border"]}}},
        ]

    def _width_requests(self, name: str, sheet_id: int, columns: int) -> list[dict[str, Any]]:
        widths = {"Programa": [170, 180, 300, 110, 140, 200, 150], "Sesiones": [90, 180, 150, 170, 220, 90, 90, 90, 90, 110, 90, 125, 150], "Participantes": [150, 190, 230, 220, 120, 110, 120, 130, 100, 100], "Ranking": [70, 150, 190, 220, 120, 100, 110, 100, 120, 110, 130], "Control": [90, 190, 170, 140, 140, 150, 190, 130], "Seguimiento individual": [150, 190, 220, 110, 110, 90, 100, 110, 130, 110, 110, 100, 300, 300, 100]}.get(name, [])
        return [{"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1}, "properties": {"pixelSize": width}, "fields": "pixelSize"}} for i, width in enumerate(widths[:columns])]

    def _number_requests(self, name: str, sheet_id: int, rows: int, headers: list[str]) -> list[dict[str, Any]]:
        requests = []
        for index, header in enumerate(headers):
            if header == "score" or header == "score_total":
                pattern, kind = "0.0", "NUMBER"
            elif header in {"rank", "session_number", "voice_total", "voice_valid", "chat_total", "chat_valid", "ambiguous_total", "sessions_with_activity", "voice_valid_total", "chat_valid_total", "countability_ruleset_version"}:
                pattern, kind = "0", "NUMBER"
            else:
                continue
            requests.append({"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": rows, "startColumnIndex": index, "endColumnIndex": index + 1}, "cell": {"userEnteredFormat": {"numberFormat": {"type": kind, "pattern": pattern}}}, "fields": "userEnteredFormat.numberFormat"}})
        return requests

    def _conditional_requests(self, name: str, sheet_id: int, rows: int, headers: list[str], metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
        existing = metadata.get("conditionalFormats", [])
        if existing:
            return []
        rules = []
        def col(header: str) -> int | None:
            return headers.index(header) if header in headers else None
        if name == "Participantes" and col("role") is not None:
            role_col = headers.index("role")
            for role, color in (("participant", PALETTE["very_light"]), ("facilitator", PALETTE["facilitator"]), ("other", PALETTE["neutral"])):
                rules.append(self._formula_rule(sheet_id, 1, rows, role_col, role_col + 1, f'=${_column(role_col + 1)}2="{role}"', color))
        if name == "Sesiones" and col("scoring_complete") is not None:
            c = headers.index("scoring_complete")
            rules.append(self._formula_rule(sheet_id, 1, rows, c, c + 1, f'=${_column(c + 1)}2=FALSE', PALETTE["warning"]))
        if name == "Ranking":
            c = headers.index("rank") if "rank" in headers else None
            if c is not None:
                for rank, color in ((1, PALETTE["warning"]), (2, PALETTE["light"]), (3, PALETTE["neutral"])):
                    rules.append(self._formula_rule(sheet_id, 1, 4, c, c + 1, f'=${_column(c + 1)}2={rank}', color))
        if name == "Control" and col("processing_status") is not None:
            c = headers.index("processing_status")
            for status, color in (("PROCESSED", PALETTE["success"]), ("PENDING", PALETTE["neutral"]), ("PROCESSING", PALETTE["processing"]), ("INCOMPLETE", PALETTE["warning"]), ("NEEDS_REVIEW", PALETTE["warning"]), ("FAILED", PALETTE["error"])):
                rules.append(self._formula_rule(sheet_id, 1, rows, c, c + 1, f'=${_column(c + 1)}2="{status}"', color))
        if name == "Seguimiento individual":
            c = col("Seguimiento")
            if c is not None:
                data_start = 4
                formula_row = data_start + 1
                for level, color in (("Normal", PALETTE["success"]), ("Observar", PALETTE["warning"]), ("Crítico", PALETTE["error"]), ("—", PALETTE["neutral"])):
                    rules.append(self._formula_rule(sheet_id, data_start, rows, c, c + 1, f'=${_column(c + 1)}{formula_row}="{level}"', color))
                enrollment = col("Matrícula")
                if enrollment is not None:
                    rules.append(self._formula_rule(sheet_id, data_start, rows, 1, c + 1, f'=${_column(enrollment + 1)}{formula_row}="inactive"', PALETTE["neutral"]))
        return [{"addConditionalFormatRule": {"rule": rule, "index": index}} for index, rule in enumerate(rules)]

    def _participant_validations(self, sheet_id: int, rows: int, headers: list[str]) -> list[dict[str, Any]]:
        requests = []
        for header, condition in (("enrollment_status", {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": "active"}, {"userEnteredValue": "inactive"}]}), ("start_session", {"type": "NUMBER_GREATER_THAN_EQ", "values": [{"userEnteredValue": "1"}]}), ("end_session", {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": '=OR(J2="",J2>=I2)'}]})):
            if header in headers:
                col = headers.index(header)
                requests.append({"setDataValidation": {"range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": rows, "startColumnIndex": col, "endColumnIndex": col + 1}, "rule": {"condition": condition, "strict": True, "showCustomUi": True}}})
        return requests

    def _follow_up_requests(self, sheet_id: int, props: Mapping[str, Any], metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
        columns = len(HEADERS["Seguimiento individual"])
        rows = max(int(props.get("gridProperties", {}).get("rowCount", 1000)), 5)
        requests = [
            {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {"hideGridlines": True, "frozenRowCount": 4, "frozenColumnCount": 2}}, "fields": "gridProperties(hideGridlines,frozenRowCount,frozenColumnCount)"}},
            {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": columns}, "cell": {"userEnteredFormat": {"backgroundColor": PALETTE["dark"], "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True, "fontSize": 12}}}, "fields": "userEnteredFormat"}},
            {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 3, "endRowIndex": 4, "startColumnIndex": 0, "endColumnIndex": columns}, "cell": {"userEnteredFormat": {"backgroundColor": PALETTE["light"], "textFormat": {"bold": True}, "horizontalAlignment": "CENTER"}}, "fields": "userEnteredFormat"}},
            {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1}, "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
            {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 13, "endIndex": 14}, "properties": {"pixelSize": 300}, "fields": "pixelSize"}},
            {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 4, "endRowIndex": rows, "startColumnIndex": 6, "endColumnIndex": 7}, "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}}, "fields": "userEnteredFormat.numberFormat"}},
            {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 4, "endRowIndex": rows, "startColumnIndex": 9, "endColumnIndex": 10}, "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0.0"}}}, "fields": "userEnteredFormat.numberFormat"}},
            {"setBasicFilter": {"filter": {"range": {"sheetId": sheet_id, "startRowIndex": 3, "endRowIndex": rows, "startColumnIndex": 0, "endColumnIndex": columns}}}},
        ]
        return requests + self._conditional_requests("Seguimiento individual", sheet_id, rows, HEADERS["Seguimiento individual"], metadata)

    @staticmethod
    def _formula_rule(sheet_id: int, start_row: int, end_row: int, start_col: int, end_col: int, formula: str, color: dict[str, float]) -> dict[str, Any]:
        return {"ranges": [{"sheetId": sheet_id, "startRowIndex": start_row, "endRowIndex": end_row, "startColumnIndex": start_col, "endColumnIndex": end_col}], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]}, "format": {"backgroundColor": color}}}


def _column(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result
