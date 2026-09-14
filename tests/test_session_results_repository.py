import unittest
from decimal import Decimal

from src.sync.scoring import ParticipantSessionScore
from src.sync.session_results_repository import (
    CANONICAL_SESSION_HEADERS,
    GoogleSheetsSessionResultsGateway,
    ParticipantSnapshot,
    SessionResultsRepository,
)


LEGACY_HEADERS = [
    "session_number", "session_name", "participant", "email", "voice_total",
    "voice_valid", "chat_total", "chat_valid", "score",
]


class FakeGateway:
    def __init__(self, values=None):
        self.values = [list(row) for row in (values or [])]
        self.reads = 0
        self.writes = 0
        self.clears = 0
        self.fail_after_rows = None

    def read_values(self):
        self.reads += 1
        return [list(row) for row in self.values]

    def write_cells(self, start_row, start_column, values):
        self.writes += 1
        while len(self.values) < start_row - 1 + len(values):
            self.values.append([])
        for row_offset, incoming in enumerate(values):
            row_index = start_row - 1 + row_offset
            row = self.values[row_index]
            while len(row) < start_column - 1 + len(incoming):
                row.append("")
            row[start_column - 1:start_column - 1 + len(incoming)] = incoming

    def clear_cells(self, start_row, start_column, end_row, end_column):
        self.clears += 1
        del self.values[start_row - 1:end_row]

    def apply_changes(self, updates, inserts, deletes):
        self.writes += 1
        if self.fail_after_rows is not None:
            for index, row in enumerate(inserts):
                if index >= self.fail_after_rows:
                    break
                self.values.append(list(row))
            raise RuntimeError("interrupción simulada")
        for row_number, fields in updates.items():
            row = self.values[row_number - 1]
            for column, value in fields.items():
                while len(row) <= column:
                    row.append("")
                row[column] = value
        for row in inserts:
            self.values.append(list(row))
        for row_number in sorted(deletes, reverse=True):
            del self.values[row_number - 1]


class HttpError(Exception):
    def __init__(self, status):
        self.resp = type("Response", (), {"status": status})()


class FakeRequest:
    def __init__(self, service, result=None):
        self.service = service
        self.result = result or {}

    def execute(self):
        self.service.executions.append(self)
        if self.service.errors:
            error = self.service.errors.pop(0)
            if error is not None:
                raise error
        return self.result


class FakeGoogleValues:
    def __init__(self, service):
        self.service = service

    def get(self, **kwargs):
        self.service.calls.append(("get", kwargs))
        return FakeRequest(self.service, {"values": self.service.values})

    def update(self, **kwargs):
        self.service.calls.append(("update", kwargs))
        return FakeRequest(self.service)

    def batchUpdate(self, **kwargs):
        self.service.calls.append(("batchUpdateValues", kwargs))
        return FakeRequest(self.service)

    def append(self, **kwargs):
        self.service.calls.append(("append", kwargs))
        return FakeRequest(self.service)


class FakeGoogleSpreadsheets:
    def __init__(self, service):
        self.service = service
        self._values = FakeGoogleValues(service)

    def values(self):
        return self._values

    def batchUpdate(self, **kwargs):
        self.service.calls.append(("batchUpdate", kwargs))
        return FakeRequest(self.service)


class FakeGoogleService:
    def __init__(self, values=None):
        self.values = values or []
        self.calls = []
        self.executions = []
        self.errors = []
        self._spreadsheets = FakeGoogleSpreadsheets(self)

    def spreadsheets(self):
        return self._spreadsheets


def score(session, participant_id, voice_total=1, voice_valid=1, chat_total=0, chat_valid=0, ambiguous=0, score="1.0", complete=True):
    return ParticipantSessionScore(session, participant_id, voice_total, voice_valid, chat_total, chat_valid, ambiguous, Decimal(score), complete)


def snapshot(participant_id, name=None, email=""):
    return ParticipantSnapshot(participant_id, name or participant_id, email)


class SessionResultsRepositoryTests(unittest.TestCase):
    def test_legacy_empty_schema_migrates_by_appending_missing_columns(self):
        gateway = FakeGateway([LEGACY_HEADERS])
        repository = SessionResultsRepository(gateway)
        repository.migrate()
        self.assertEqual(gateway.values[0], LEGACY_HEADERS + ["participant_id", "ambiguous_total", "scoring_complete", "countability_ruleset_version"])

    def test_repeated_migration_is_idempotent(self):
        gateway = FakeGateway([LEGACY_HEADERS])
        repository = SessionResultsRepository(gateway)
        repository.migrate()
        first = gateway.values[0]
        writes = gateway.writes
        repository.migrate()
        self.assertEqual(gateway.values[0], first)
        self.assertEqual(gateway.writes, writes)

    def test_unknown_columns_are_preserved(self):
        headers = LEGACY_HEADERS + ["manual_note"]
        gateway = FakeGateway([headers])
        repository = SessionResultsRepository(gateway)
        repository.migrate()
        self.assertEqual(gateway.values[0][:len(headers)], headers)
        self.assertEqual(gateway.values[0][len(headers):], ["participant_id", "ambiguous_total", "scoring_complete", "countability_ruleset_version"])

    def test_unknown_column_values_are_preserved_for_other_sessions(self):
        headers = CANONICAL_SESSION_HEADERS + ["manual_note"]
        old = [2, "Sesión 2", "p2", "Beto", "", 1, 1, 0, 0, 0, "1.0", True, 1, "conservar"]
        gateway = FakeGateway([headers, old])
        repository = SessionResultsRepository(gateway)
        repository.replace_session(1, "Sesión 1", [score(1, "p1")], {"p1": snapshot("p1")})
        self.assertEqual(gateway.values[1][-1], "conservar")

    def test_unknown_formula_is_preserved_when_target_metrics_update(self):
        headers = CANONICAL_SESSION_HEADERS + ["manual_formula"]
        old = [1, "Sesión 1", "p1", "Ana", "", 1, 1, 0, 0, 0, "1.0", True, 1, "=SUM(A1:A2)"]
        gateway = FakeGateway([headers, old])
        repository = SessionResultsRepository(gateway)
        updated = score(1, "p1", voice_total=2, voice_valid=2, score="2.0")
        repository.replace_session(1, "Sesión 1", [updated], {"p1": snapshot("p1", "Ana")})
        self.assertEqual(gateway.values[1][5], 2)
        self.assertEqual(gateway.values[1][-1], "=SUM(A1:A2)")

    def test_insert_session_with_multiple_participants(self):
        gateway = FakeGateway([CANONICAL_SESSION_HEADERS])
        repository = SessionResultsRepository(gateway)
        results = [score(1, "p1"), score(1, "p2", chat_total=2, chat_valid=2, score="1.0")]
        outcome = repository.replace_session(1, "Sesión 1", results, {"p1": snapshot("p1", "Ana", "a@example.test"), "p2": snapshot("p2", "Beto")})
        self.assertEqual(outcome.status, "REPLACE")
        self.assertEqual(len(gateway.values), 3)
        self.assertEqual(gateway.values[1][2], "p1")
        self.assertEqual(gateway.values[2][2], "p2")

    def test_second_execution_is_noop_without_duplicate(self):
        gateway = FakeGateway([CANONICAL_SESSION_HEADERS])
        repository = SessionResultsRepository(gateway)
        results = [score(1, "p1")]
        snapshots = {"p1": snapshot("p1", "Ana")}
        self.assertEqual(repository.replace_session(1, "Sesión 1", results, snapshots).status, "REPLACE")
        writes = gateway.writes
        self.assertEqual(repository.replace_session(1, "Sesión 1", results, snapshots).status, "NOOP")
        self.assertEqual(gateway.writes, writes)
        self.assertEqual(len(gateway.values), 2)

    def test_same_logical_key_does_not_duplicate(self):
        gateway = FakeGateway([CANONICAL_SESSION_HEADERS])
        repository = SessionResultsRepository(gateway)
        inputs = [score(1, "p1")]
        snapshots = {"p1": snapshot("p1", "Ana")}
        repository.replace_session(1, "Sesión 1", inputs, snapshots)
        repository.replace_session(1, "Sesión 1", inputs, snapshots)
        keys = [(row[0], row[2]) for row in gateway.values[1:]]
        self.assertEqual(keys, [(1, "p1")])

    def test_reprocess_updates_metrics(self):
        gateway = FakeGateway([CANONICAL_SESSION_HEADERS])
        repository = SessionResultsRepository(gateway)
        repository.replace_session(1, "Sesión 1", [score(1, "p1")], {"p1": snapshot("p1", "Ana")})
        updated = score(1, "p1", voice_total=2, voice_valid=2, score="2.0")
        repository.replace_session(1, "Sesión 1", [updated], {"p1": snapshot("p1", "Ana")})
        self.assertEqual(gateway.values[1][5:9], [2, 2, 0, 0])
        self.assertEqual(gateway.values[1][10], "2.0")

    def test_reprocess_removes_obsolete_rows_only_in_target_session(self):
        gateway = FakeGateway([CANONICAL_SESSION_HEADERS])
        repository = SessionResultsRepository(gateway)
        repository.replace_session(1, "Sesión 1", [score(1, "p1"), score(1, "p2")], {"p1": snapshot("p1"), "p2": snapshot("p2")})
        repository.replace_session(2, "Sesión 2", [score(2, "p3")], {"p3": snapshot("p3")})
        repository.replace_session(1, "Sesión 1", [score(1, "p1")], {"p1": snapshot("p1")})
        self.assertEqual([(row[0], row[2]) for row in gateway.values[1:]], [(1, "p1"), (2, "p3")])

    def test_participant_id_is_required(self):
        gateway = FakeGateway([CANONICAL_SESSION_HEADERS])
        repository = SessionResultsRepository(gateway)
        with self.assertRaises(ValueError):
            repository.replace_session(1, "Sesión 1", [score(1, "")], {})

    def test_legacy_row_without_participant_id_is_blocking(self):
        gateway = FakeGateway([LEGACY_HEADERS, [1, "Sesión 1", "Ana", "", 1, 1, 0, 0, "1.0"]])
        repository = SessionResultsRepository(gateway)
        with self.assertRaisesRegex(ValueError, "NEEDS_MIGRATION"):
            repository.migrate()
        self.assertEqual(gateway.writes, 0)

    def test_existing_duplicate_logical_key_is_rejected(self):
        row = [1, "Sesión 1", "p1", "Ana", "", 1, 1, 0, 0, 0, "1.0", True, 1]
        gateway = FakeGateway([CANONICAL_SESSION_HEADERS, row, row])
        repository = SessionResultsRepository(gateway)
        with self.assertRaisesRegex(ValueError, "Resultado duplicado"):
            repository.migrate()

    def test_exact_score_ambiguous_complete_and_ruleset_are_persisted(self):
        gateway = FakeGateway([CANONICAL_SESSION_HEADERS])
        repository = SessionResultsRepository(gateway)
        value = score(1, "p1", ambiguous=1, score="0.5", complete=False)
        repository.replace_session(1, "Sesión 1", [value], {"p1": snapshot("p1")}, ruleset_version=1)
        row = gateway.values[1]
        self.assertEqual(row[9:13], [1, "0.5", False, 1])

    def test_batch_reads_and_writes_are_bounded(self):
        gateway = FakeGateway([CANONICAL_SESSION_HEADERS])
        repository = SessionResultsRepository(gateway)
        repository.replace_session(1, "Sesión 1", [score(1, str(i)) for i in range(49)], {str(i): snapshot(str(i)) for i in range(49)})
        self.assertLessEqual(gateway.reads, 3)
        self.assertEqual(gateway.writes, 1)

    def test_interruption_then_retry_has_no_duplicates(self):
        gateway = FakeGateway([CANONICAL_SESSION_HEADERS])
        repository = SessionResultsRepository(gateway)
        results = [score(1, str(i)) for i in range(49)]
        snapshots = {str(i): snapshot(str(i)) for i in range(49)}
        gateway.fail_after_rows = 29
        with self.assertRaises(RuntimeError):
            repository.replace_session(1, "Sesión 1", results, snapshots)
        gateway.fail_after_rows = None
        repository.replace_session(1, "Sesión 1", results, snapshots)
        self.assertEqual(len(gateway.values), 50)
        self.assertEqual(len({row[2] for row in gateway.values[1:]}), 49)

    def test_score_half_is_preserved(self):
        gateway = FakeGateway([CANONICAL_SESSION_HEADERS])
        repository = SessionResultsRepository(gateway)
        repository.replace_session(1, "Sesión 1", [score(1, "p1", score="0.5")], {"p1": snapshot("p1")})
        self.assertEqual(gateway.values[1][10], "0.5")

    def test_snapshot_does_not_change_logical_identity(self):
        gateway = FakeGateway([CANONICAL_SESSION_HEADERS])
        repository = SessionResultsRepository(gateway)
        repository.replace_session(1, "Sesión 1", [score(1, "p1")], {"p1": snapshot("p1", "Ana", "old@example.test")})
        repository.replace_session(1, "Sesión 1", [score(1, "p1")], {"p1": snapshot("p1", "Ana nueva", "new@example.test")})
        self.assertEqual(len(gateway.values), 2)
        self.assertEqual(gateway.values[1][2], "p1")


class GoogleSheetsSessionResultsGatewayTests(unittest.TestCase):
    def gateway(self, service=None):
        return GoogleSheetsSessionResultsGateway(service or FakeGoogleService(), "sheet-id", 42)

    def test_read_is_one_batch_values_call(self):
        service = FakeGoogleService([["header"]])
        self.assertEqual(self.gateway(service).read_values(), [["header"]])
        self.assertEqual([kind for kind, _ in service.calls], ["get"])
        self.assertEqual(service.calls[0][1]["range"], "'Sesiones'!A:ZZ")

    def test_updates_are_one_values_batch_call(self):
        service = FakeGoogleService()
        self.gateway(service).apply_changes({5: {0: 1, 2: "p1", 3: "Ana"}}, [], [])
        calls = [kind for kind, _ in service.calls]
        self.assertEqual(calls, ["batchUpdateValues"])
        payload = service.calls[0][1]["body"]
        self.assertEqual(payload["valueInputOption"], "RAW")
        self.assertEqual(len(payload["data"]), 2)
        self.assertNotIn("manual_note", str(payload))

    def test_insert_is_one_append_batch_call(self):
        service = FakeGoogleService()
        self.gateway(service).apply_changes({}, [[1, "Sesión 1", "p1"] for _ in range(49)], [])
        self.assertEqual([kind for kind, _ in service.calls], ["append"])
        self.assertEqual(len(service.calls[0][1]["body"]["values"]), 49)

    def test_deletes_are_one_descending_batch_update(self):
        service = FakeGoogleService()
        self.gateway(service).apply_changes({}, [], [12, 4, 9])
        self.assertEqual([kind for kind, _ in service.calls], ["batchUpdate"])
        requests = service.calls[0][1]["body"]["requests"]
        self.assertEqual([request["deleteDimension"]["range"]["startIndex"] for request in requests], [11, 8, 3])
        self.assertEqual([request["deleteDimension"]["range"]["sheetId"] for request in requests], [42, 42, 42])

    def test_header_migration_writes_only_header_range(self):
        service = FakeGoogleService()
        self.gateway(service).write_cells(1, 10, [["ambiguous_total", "scoring_complete"]])
        kind, kwargs = service.calls[0]
        self.assertEqual(kind, "update")
        self.assertEqual(kwargs["range"], "'Sesiones'!J1:K1")

    def test_retry_429_and_5xx_are_bounded(self):
        from unittest.mock import patch
        for status in (429, 503):
            with self.subTest(status=status):
                service = FakeGoogleService([["header"]])
                service.errors = [HttpError(status), None]
                with patch("src.sync.participant_repository.time.sleep") as sleep:
                    self.assertEqual(self.gateway(service).read_values(), [["header"]])
                self.assertEqual(sleep.call_count, 1)

    def test_permanent_error_is_not_retried(self):
        from unittest.mock import patch
        service = FakeGoogleService()
        service.errors = [HttpError(400), None]
        with patch("src.sync.participant_repository.time.sleep") as sleep:
            with self.assertRaises(HttpError):
                self.gateway(service).read_values()
        self.assertEqual(sleep.call_count, 0)


if __name__ == "__main__":
    unittest.main()
