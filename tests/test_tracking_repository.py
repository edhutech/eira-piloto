import unittest
from decimal import Decimal
from types import SimpleNamespace

from src.sync.models import SessionRecord
from src.sync.participants import Participant
from src.sync.scoring import ParticipantSessionScore
from src.sync.tracking_repository import TrackingParticipant, TrackingRepository, TrackingSession, TrackingView, build_tracking_view, tracking_values


class FakeGateway:
    def __init__(self, values=None):
        self.values = values or []
        self.calls = []
    def ensure_sheet(self, session_count):
        self.calls.append(("ensure", session_count))
        return not self.values, 7
    def read_values(self):
        return [list(row) for row in self.values]
    def persist(self, values, manual_cells, structural_change, worksheet_id):
        self.calls.append(("persist", structural_change, manual_cells))
        self.values = [list(row) for row in values]
        return SimpleNamespace(status="REPLACE" if structural_change else "NOOP", rows_written=max(0, len(values)-5))


class FakeParticipants:
    def __init__(self, values): self.values = values
    def load(self): return list(self.values)


class FakeSessions:
    def __init__(self, values): self.values = values
    def load_scores(self): return list(self.values)


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.sessions = [SessionRecord(1, "Clase 1", "s1"), SessionRecord(2, "Clase 2", "s2")]
        self.participants = [
            Participant("p2", "Zoe", "z@example.com"),
            Participant("p1", "Ana", "a@example.com"),
            Participant("f1", "Facilitador", role="facilitator"),
            Participant("o1", "Otro", role="other"),
        ]
        self.results = [
            ParticipantSessionScore(1, "p1", 2, 2, 3, 3, 0, Decimal("3.5"), True),
            ParticipantSessionScore(1, "p2", 1, 0, 2, 0, 0, Decimal("0"), True),
            ParticipantSessionScore(1, "f1", 4, 4, 4, 4, 0, Decimal("6"), True),
        ]

    def test_only_participants_are_sorted_and_metrics_are_exact(self):
        view = build_tracking_view(self.participants, self.sessions, {1: "PROCESSED", 2: "INCOMPLETE"}, self.results, {1: 4, 2: None})
        self.assertEqual([p.participant_id for p in view.participants], ["p1", "p2"])
        first = view.sessions[0]
        self.assertEqual(first.participated, 1)
        self.assertEqual(first.participation_pct, Decimal("25"))
        self.assertEqual(first.metrics["p1"], (Decimal("1.5"), Decimal("2"), Decimal("3.5")))
        self.assertEqual(first.metrics["p2"], (Decimal("0"), Decimal("0"), Decimal("0")))
        self.assertEqual(view.sessions[1].participated, None)
        self.assertIsNone(view.sessions[1].metrics["p1"])

    def test_participated_counts_unique_people_with_valid_activity(self):
        view = build_tracking_view(self.participants, [self.sessions[0]], {1: "PROCESSED"}, self.results, {1: 1})
        self.assertEqual(view.sessions[0].participated, 1)

    def test_blank_and_zero_attendance_leave_percentage_unset(self):
        for value in (None, 0):
            view = build_tracking_view(self.participants, [self.sessions[0]], {1: "PROCESSED"}, self.results, {1: value})
            self.assertIsNone(view.sessions[0].participation_pct)

    def test_values_have_horizontal_blocks_and_percentage_formula(self):
        view = build_tracking_view(self.participants, self.sessions, {1: "PROCESSED", 2: "INCOMPLETE"}, self.results, {1: None, 2: None})
        values, manual = tracking_values(view)
        self.assertEqual(values[0][3], "Clase 1 / Clase 1")
        self.assertEqual(values[5][0:3], ["p1", "Ana", "a@example.com"])
        self.assertEqual(values[5][3:6], ["1.5", "2", "3.5"] if False else [Decimal("1.5"), Decimal("2"), Decimal("3.5")])
        self.assertIn((1, 4), manual)
        self.assertTrue(values[3][4].startswith("=IF(OR(E2"))
        self.assertEqual(values[5][6:9], ["", "", ""])

    def test_repository_preserves_manual_attendance_on_refresh(self):
        gateway = FakeGateway()
        repository = TrackingRepository(gateway, FakeParticipants(self.participants), FakeSessions(self.results))
        first = repository.refresh(self.sessions, {1: "PROCESSED", 2: "INCOMPLETE"})
        gateway.values[1][4] = 30
        second = repository.refresh(self.sessions, {1: "PROCESSED", 2: "INCOMPLETE"})
        self.assertEqual(gateway.values[1][4], 30)
        self.assertEqual(second.status, "NOOP")

    def test_role_change_removes_person_and_name_email_refreshes_snapshot(self):
        gateway = FakeGateway()
        participants = FakeParticipants(self.participants)
        repository = TrackingRepository(gateway, participants, FakeSessions(self.results))
        repository.refresh(self.sessions, {1: "PROCESSED", 2: "INCOMPLETE"})
        participants.values[1] = Participant("p1", "Ana Nueva", "new@example.com", role="facilitator")
        result = repository.refresh(self.sessions, {1: "PROCESSED", 2: "INCOMPLETE"})
        self.assertEqual(result.status, "REPLACE")
        self.assertNotIn("p1", [row[0] for row in gateway.values[5:]])
        self.assertIn("p2", [row[0] for row in gateway.values[5:]])

    def test_structural_format_contract_is_requested(self):
        view = TrackingView((TrackingParticipant("p1", "Ana", "a@example.com"),), (TrackingSession(1, "Clase", "PENDING", None, None, None, {"p1": None}),))
        values, manual = tracking_values(view)
        self.assertEqual(len(values), 6)
        self.assertEqual(len(values[0]), 6)
        self.assertEqual(manual, {(1, 4)})

    def test_structural_refresh_removes_stale_tail_rows(self):
        gateway = FakeGateway()
        repository = TrackingRepository(gateway, FakeParticipants(self.participants[:2]), FakeSessions(self.results))
        repository.refresh(self.sessions, {1: "PROCESSED", 2: "INCOMPLETE"})
        gateway.values.extend([["ghost", "Ghost", "ghost@example.com"] + [""] * 6 for _ in range(3)])
        repository.participant_repository.values[:] = self.participants[:1]
        result = repository.refresh(self.sessions, {1: "PROCESSED", 2: "INCOMPLETE"})
        self.assertEqual(result.status, "REPLACE")
        self.assertEqual(len(gateway.values), 6)


if __name__ == "__main__":
    unittest.main()
