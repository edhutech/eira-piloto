import unittest
from decimal import Decimal

from src.init_program import RANKING_HEADERS, REQUIRED_SHEETS, _sheet_values
from src.sync.participants import Participant
from src.sync.ranking import ProgramRanking, ProgramRankingEntry
from src.sync.ranking_repository import (
    GoogleSheetsRankingGateway,
    RankingRepository,
)


class FakeGateway:
    def __init__(self, values=None, exists=True):
        self.values = [list(row) for row in (values or [])]
        self.exists = exists
        self.writes = 0
        self.calls = []

    def inspect_sheet(self):
        return {"sheetId": 42, "title": "Ranking"} if self.exists else None

    def read_values(self):
        if not self.exists:
            raise RuntimeError("Ranking inexistente")
        self.calls.append("read")
        return [list(row) for row in self.values]

    def create_sheet(self):
        self.calls.append("create")
        self.exists = True

    def write_cells(self, start_row, start_column, values):
        self.writes += 1
        self.calls.append(("write_cells", start_row, start_column, values))
        while len(self.values) < start_row - 1 + len(values):
            self.values.append([])
        for offset, incoming in enumerate(values):
            row = self.values[start_row - 1 + offset]
            while len(row) < start_column - 1 + len(incoming):
                row.append("")
            row[start_column - 1:start_column - 1 + len(incoming)] = incoming

    def apply_changes(self, updates, inserts, deletes):
        self.writes += 1
        self.calls.append(("apply", updates, inserts, deletes))
        for row_number, fields in updates.items():
            for column, value in fields.items():
                self.values[row_number - 1][column] = value
        for row in inserts:
            self.values.append(list(row))
        for row_number in sorted(deletes, reverse=True):
            del self.values[row_number - 1]

    def reorder_rows(self, participant_ids):
        header, rows = self.values[0], self.values[1:]
        positions = {str(value).strip().casefold(): index for index, value in enumerate(header)}
        by_id = {str(row[positions["participant_id"]]).strip(): row for row in rows}
        self.values = [header] + [by_id[participant_id] for participant_id in participant_ids]


def participants(*entries):
    return list(entries)


def ranking(*entries, complete=True):
    return ProgramRanking(tuple(entries), complete, {})


def entry(pid, name, score, rank, complete=True, email=""):
    return ProgramRankingEntry(pid, name, "participant", 1, 1, 0, Decimal(score), rank, complete, 1, 0)


class RankingRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.people = participants(Participant("p1", "Ana", "ana@example.test"), Participant("p2", "Beto", "beto@example.test"))

    def test_missing_sheet_migration_plan_is_create_sheet(self):
        repository = RankingRepository(FakeGateway(exists=False))
        self.assertEqual(repository.migration_plan(), {"action": "CREATE_SHEET", "sheet_name": "Ranking"})

    def test_empty_sheet_migrates_headers_and_repeated_migration_is_noop(self):
        gateway = FakeGateway([RANKING_HEADERS])
        repository = RankingRepository(gateway)
        self.assertEqual(repository.migration_plan()["action"], "NOOP")
        self.assertEqual(gateway.writes, 0)

    def test_missing_headers_are_appended(self):
        headers = ["rank", "participant_id", "participant"]
        gateway = FakeGateway([headers])
        repository = RankingRepository(gateway)
        repository.migrate()
        self.assertEqual(gateway.values[0], headers + [h for h in RANKING_HEADERS if h not in headers])

    def test_legacy_row_without_id_needs_migration(self):
        gateway = FakeGateway([["rank", "participant"], [1, "Ana"]])
        with self.assertRaisesRegex(ValueError, "NEEDS_MIGRATION"):
            RankingRepository(gateway).migrate()

    def test_insert_update_delete_and_noop(self):
        gateway = FakeGateway([RANKING_HEADERS])
        repository = RankingRepository(gateway)
        first = ranking(entry("p1", "Ana", "1.0", 1), entry("p2", "Beto", "0.5", 2))
        self.assertEqual(repository.persist(first, self.people).status, "REPLACE")
        self.assertEqual(len(gateway.values), 3)
        updated = ranking(entry("p2", "Beto", "2.0", 1), complete=True)
        self.assertEqual(repository.persist(updated, self.people).status, "REPLACE")
        self.assertEqual(len(gateway.values), 2)
        writes = gateway.writes
        self.assertEqual(repository.persist(updated, self.people).status, "NOOP")
        self.assertEqual(gateway.writes, writes)

    def test_participant_to_facilitator_is_absent_from_desired_view(self):
        gateway = FakeGateway([RANKING_HEADERS])
        repository = RankingRepository(gateway)
        repository.persist(ranking(entry("p1", "Ana", "1", 1)), self.people)
        remaining = ranking()
        self.assertEqual(repository.persist(remaining, self.people).status, "REPLACE")
        self.assertEqual(len(gateway.values), 1)

    def test_physical_order_follows_current_ranking(self):
        gateway = FakeGateway([RANKING_HEADERS])
        repository = RankingRepository(gateway)
        repository.persist(ranking(entry("p1", "Ana", "1", 1), entry("p2", "Beto", "0.5", 2)), self.people)
        repository.persist(ranking(entry("p2", "Beto", "2", 1), entry("p1", "Ana", "1", 2)), self.people)
        self.assertEqual([row[1] for row in gateway.values[1:]], ["p2", "p1"])

    def test_name_and_email_are_current_snapshots(self):
        gateway = FakeGateway([RANKING_HEADERS])
        repository = RankingRepository(gateway)
        repository.persist(ranking(entry("p1", "old", "0.5", 1)), self.people)
        current = participants(Participant("p1", "Nombre actual", "new@example.test"))
        repository.persist(ranking(entry("p1", "ignored", "0.5", 1)), current)
        row = gateway.values[1]
        self.assertEqual((row[2], row[3]), ("Nombre actual", "new@example.test"))
        self.assertEqual(row[1], "p1")

    def test_unknown_columns_and_formula_are_preserved(self):
        headers = RANKING_HEADERS + ["manual_note", "manual_formula"]
        row = [1, "p1", "Ana", "old@example.test", 1, 1, 1, 0, 0, "1.0", True, "nota", "=SUM(A1:A2)"]
        gateway = FakeGateway([headers, row])
        repository = RankingRepository(gateway)
        repository.persist(ranking(entry("p1", "Ana", "2.0", 1)), self.people)
        self.assertEqual(gateway.values[1][-2:], ["nota", "=SUM(A1:A2)"])

    def test_validation_errors_are_explicit(self):
        repository = RankingRepository(FakeGateway([RANKING_HEADERS]))
        with self.assertRaises(ValueError):
            repository.persist(ranking(entry("missing", "X", "1", 1)), self.people)
        with self.assertRaisesRegex(ValueError, "facilitator"):
            repository.persist(ranking(ProgramRankingEntry("p1", "Ana", "facilitator", 1, 1, 0, Decimal("1"), 1, True)), self.people)
        with self.assertRaisesRegex(ValueError, "duplicado"):
            repository.persist(ranking(entry("p1", "Ana", "1", 1), entry("p1", "Ana", "1", 1)), self.people)

    def test_incomplete_and_half_point_are_persisted(self):
        gateway = FakeGateway([RANKING_HEADERS])
        repository = RankingRepository(gateway)
        repository.persist(ranking(entry("p1", "Ana", "0.5", 1, complete=False), complete=False), self.people)
        self.assertEqual(gateway.values[1][9:11], ["0.5", False])

    def test_numeric_and_boolean_sheet_strings_produce_noop(self):
        row = ["1", "p1", "Ana", "ana@example.test", "1", "1", "1", "0", "0", "0.50", "TRUE"]
        gateway = FakeGateway([RANKING_HEADERS, row])
        repository = RankingRepository(gateway)
        outcome = repository.persist(ranking(entry("p1", "Ana", "0.5", 1)), self.people)
        self.assertEqual(outcome.status, "NOOP")
        self.assertEqual(gateway.writes, 0)

    def test_gateway_deletes_descending_and_retries_shared_helper(self):
        service = FakeGoogleService()
        gateway = GoogleSheetsRankingGateway(service, "sheet-id", 42)
        gateway.apply_changes({}, [], [12, 4, 9])
        requests = service.calls[0][1]["body"]["requests"]
        self.assertEqual([r["deleteDimension"]["range"]["startIndex"] for r in requests], [11, 8, 3])


class FakeRequest:
    def __init__(self, service, result=None):
        self.service, self.result = service, result or {}
    def execute(self):
        if self.service.errors:
            error = self.service.errors.pop(0)
            if error is not None:
                raise error
        return self.result


class FakeGoogleValues:
    def __init__(self, service): self.service = service
    def get(self, **kwargs): return FakeRequest(self.service, {"values": []})
    def update(self, **kwargs): self.service.calls.append(("update", kwargs)); return FakeRequest(self.service)
    def batchUpdate(self, **kwargs): self.service.calls.append(("batchUpdateValues", kwargs)); return FakeRequest(self.service)
    def append(self, **kwargs): self.service.calls.append(("append", kwargs)); return FakeRequest(self.service)


class FakeGoogleSpreadsheets:
    def __init__(self, service): self.service, self._values = service, FakeGoogleValues(service)
    def values(self): return self._values
    def batchUpdate(self, **kwargs): self.service.calls.append(("batchUpdate", kwargs)); return FakeRequest(self.service)
    def get(self, **kwargs): return FakeRequest(self.service, {"sheets": [{"properties": {"sheetId": 42, "title": "Ranking"}}]})


class FakeGoogleService:
    def __init__(self): self.calls, self.errors = [], []
    def spreadsheets(self): return FakeGoogleSpreadsheets(self)


class InitRankingTests(unittest.TestCase):
    def test_new_program_includes_ranking_tab_in_order(self):
        self.assertEqual(REQUIRED_SHEETS, ["Seguimiento", "Programa", "Sesiones", "Participantes", "Ranking", "Control"])
        self.assertEqual(_sheet_values.__name__, "_sheet_values")
        plan = type("Plan", (), {"program_name": "P", "folder_id": "f", "folder_url": "u", "session_count": 1, "participant_mode": "auto", "participants": []})()
        values = _sheet_values(plan, [])
        self.assertEqual(values["Ranking"], [RANKING_HEADERS])


class HttpError(Exception):
    def __init__(self, status):
        self.resp = type("Response", (), {"status": status})()


class RankingGatewayTests(unittest.TestCase):
    def test_inspect_finds_ranking_sheet(self):
        service = FakeGoogleService()
        gateway = GoogleSheetsRankingGateway(service, "sheet-id")
        properties = gateway.inspect_sheet()
        self.assertEqual(properties, {"sheetId": 42, "title": "Ranking"})
        self.assertEqual(gateway.worksheet_id, 42)

    def test_update_and_insert_use_batch_calls(self):
        service = FakeGoogleService()
        GoogleSheetsRankingGateway(service, "sheet-id", 42).apply_changes(
            {3: {1: "p1", 9: "1.0"}}, [[1, "p2"]], []
        )
        self.assertEqual([kind for kind, _ in service.calls], ["batchUpdateValues", "append"])
        self.assertEqual(service.calls[0][1]["body"]["valueInputOption"], "RAW")
        self.assertEqual(len(service.calls[0][1]["body"]["data"]), 2)
        self.assertEqual(service.calls[1][1]["body"]["values"], [[1, "p2"]])

    def test_retry_429_and_5xx_and_permanent_error(self):
        from unittest.mock import patch
        for status in (429, 503):
            service = FakeGoogleService()
            service.errors = [HttpError(status), None]
            with self.subTest(status=status), patch("src.sync.participant_repository.time.sleep") as sleep:
                self.assertEqual(GoogleSheetsRankingGateway(service, "sheet-id").read_values(), [])
                self.assertEqual(sleep.call_count, 1)
        service = FakeGoogleService()
        service.errors = [HttpError(400), None]
        with patch("src.sync.participant_repository.time.sleep") as sleep:
            with self.assertRaises(HttpError):
                GoogleSheetsRankingGateway(service, "sheet-id").read_values()
            self.assertEqual(sleep.call_count, 0)


if __name__ == "__main__":
    unittest.main()
