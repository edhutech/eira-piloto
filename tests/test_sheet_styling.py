import unittest
from typing import Any

from participacion.adapters.google.sheets.styling import GoogleSheetStyler, PALETTE


class Request:
    def __init__(self, callback): self.callback = callback
    def execute(self): return self.callback()


class FakeSheets:
    def __init__(self):
        self.calls = []
        self.metadata = {"sheets": [
            {"properties": {"title": "Programa", "sheetId": 1, "gridProperties": {"rowCount": 20, "columnCount": 6}}},
            {"properties": {"title": "Sesiones", "sheetId": 2, "gridProperties": {"rowCount": 20, "columnCount": 13}}},
            {"properties": {"title": "Participantes", "sheetId": 3, "gridProperties": {"rowCount": 20, "columnCount": 7}}},
            {"properties": {"title": "Ranking", "sheetId": 4, "gridProperties": {"rowCount": 20, "columnCount": 11}}},
            {"properties": {"title": "Control", "sheetId": 5, "gridProperties": {"rowCount": 20, "columnCount": 7}}},
            {"properties": {"title": "Seguimiento", "sheetId": 6, "gridProperties": {"rowCount": 20, "columnCount": 18}}},
        ]}
    def get(self, **kwargs): return Request(lambda: self.metadata)
    def batchUpdate(self, **kwargs):
        body = kwargs["body"]
        self.calls.append(body["requests"])
        def done():
            for item in self.metadata["sheets"]:
                item_any: Any = item
                item_any["conditionalFormats"] = [{}]
                if item_any["properties"]["title"] != "Programa":
                    item_any["basicFilter"] = {"range": {}}
            return {}
        return Request(done)


class FakeService:
    def __init__(self): self._sheets = FakeSheets()
    def spreadsheets(self): return self._sheets


class SheetStylingTests(unittest.TestCase):
    def test_all_tabs_get_global_visual_requests(self):
        service = FakeService()
        result = GoogleSheetStyler(service, "sheet").apply()
        self.assertEqual(set(result.sheets), {"Seguimiento", "Programa", "Sesiones", "Participantes", "Ranking", "Control"})
        requests = service._sheets.calls[0]
        self.assertTrue(any("updateSheetProperties" in r for r in requests))
        self.assertTrue(any("repeatCell" in r for r in requests))
        self.assertTrue(any("updateDimensionProperties" in r for r in requests))
        self.assertTrue(any("updateBorders" in r for r in requests))

    def test_technical_tabs_have_filters_and_expected_formats(self):
        styler = GoogleSheetStyler(FakeService(), "sheet")
        requests = styler._sheet_requests("Sesiones", 2, {"gridProperties": {"rowCount": 20}}, {})
        self.assertTrue(any("setBasicFilter" in r for r in requests))
        formats = [r["repeatCell"]["cell"]["userEnteredFormat"] for r in requests if "repeatCell" in r]
        self.assertTrue(any(f.get("numberFormat", {}).get("pattern") == "0.0" for f in formats))
        self.assertTrue(any(r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {}).get("backgroundColor") == PALETTE["dark"] for r in requests))

    def test_conditional_rules_cover_roles_statuses_and_ranking(self):
        styler = GoogleSheetStyler(FakeService(), "sheet")
        participants = styler._conditional_requests("Participantes", 3, 20, ["participant_id", "nombre", "correo", "aliases", "role", "source", "status"], {})
        control = styler._conditional_requests("Control", 5, 20, ["session_number", "session_name", "folder_id", "transcript_status", "chat_status", "processing_status", "last_processed_at"], {})
        ranking = styler._conditional_requests("Ranking", 4, 20, ["rank", "participant_id", "participant", "email", "score_total"], {})
        self.assertEqual(len(participants), 3)
        self.assertEqual(len(control), 6)
        self.assertEqual(len(ranking), 3)
        self.assertEqual(styler._conditional_requests("Control", 5, 20, ["processing_status"], {"conditionalFormats": [{}]}), [])

    def test_second_application_does_not_add_conditional_rules_again(self):
        service = FakeService()
        styler = GoogleSheetStyler(service, "sheet")
        styler.apply()
        styler.apply()
        second = service._sheets.calls[1]
        self.assertFalse(any("addConditionalFormatRule" in r for r in second))
        self.assertTrue(any("setBasicFilter" in r for r in second))
        self.assertTrue(any("clearBasicFilter" in r for r in second))


if __name__ == "__main__":
    unittest.main()
