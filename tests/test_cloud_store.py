import unittest

from participacion.adapters.google.sheets.cloud_store import (
    GoogleSheetsJsonStore,
    GoogleSheetsStateStore,
)


class Request:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class Values:
    def __init__(self):
        self.rows = []

    def get(self, **kwargs):
        return Request(lambda: {"values": [list(row) for row in self.rows]})

    def update(self, **kwargs):
        incoming = kwargs["body"]["values"]
        target = kwargs["range"]
        def apply():
            if target.endswith("A1:C1"):
                self.rows = [list(incoming[0])] + self.rows[1:]
            else:
                row_number = int(target.split("!A", 1)[1].split(":", 1)[0])
                while len(self.rows) < row_number:
                    self.rows.append([])
                self.rows[row_number - 1] = list(incoming[0])
            return {}
        return Request(apply)

    def append(self, **kwargs):
        incoming = kwargs["body"]["values"]
        return Request(lambda: self.rows.extend([list(row) for row in incoming]) or {})


class Spreadsheets:
    def __init__(self, values):
        self._values = values

    def values(self):
        return self._values


class Service:
    def __init__(self):
        self.values_api = Values()
        self.sheets_api = Spreadsheets(self.values_api)

    def spreadsheets(self):
        return self.sheets_api


class CloudStoreTests(unittest.TestCase):
    def test_json_store_roundtrip_and_replace(self):
        service = Service()
        store = GoogleSheetsJsonStore(service, "sheet", "Configuración")
        store.put("bundle", {"version": 1})
        self.assertEqual(store.get("bundle"), {"version": 1})
        store.put("bundle", {"version": 2})
        self.assertEqual(store.get("bundle"), {"version": 2})
        self.assertEqual(len(service.values_api.rows), 2)

    def test_state_store_roundtrip(self):
        service = Service()
        state = GoogleSheetsStateStore(
            GoogleSheetsJsonStore(service, "sheet", "Estado")
        )
        self.assertEqual(state.load(), {"version": 1, "programs": {}})
        expected = {"version": 1, "programs": {"p": {"sessions": {}}}}
        state.save(expected)
        self.assertEqual(state.load(), expected)

    def test_invalid_header_fails_closed(self):
        service = Service()
        service.values_api.rows = [["bad", "header"]]
        store = GoogleSheetsJsonStore(service, "sheet", "Configuración")
        with self.assertRaisesRegex(ValueError, "encabezados exactos"):
            store.load_all()

    def test_duplicate_keys_fail_closed(self):
        service = Service()
        service.values_api.rows = [
            ["key", "value_json", "updated_at"],
            ["bundle", "{}", ""],
            ["bundle", "{}", ""],
        ]
        store = GoogleSheetsJsonStore(service, "sheet", "Configuración")
        with self.assertRaisesRegex(ValueError, "duplicada"):
            store.load_all()


if __name__ == "__main__":
    unittest.main()
