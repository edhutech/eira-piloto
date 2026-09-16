import unittest

from participacion.adapters.google.sheets.follow_up import (FOLLOW_UP_HEADERS, FollowUpRepository,
                                           FollowUpWriteResult)
from participacion.core.models import SessionRecord
from participacion.core.participants import Participant


class Gateway:
    def __init__(self):
        self.values = []

    def ensure_sheet(self):
        return (not self.values, 1)

    def read_values(self):
        return [list(row) for row in self.values]

    def persist(self, values, manual_cells, structural_change, worksheet_id):
        if self.values and not structural_change:
            for r, c in manual_cells:
                values[r][c] = self.values[r][c]
        changed = values != self.values
        self.values = [list(row) for row in values]
        return FollowUpWriteResult("REPLACE" if changed else "NOOP", max(0, len(values) - 4))


class People:
    def __init__(self):
        self.people = [Participant("p1", "Ana")]
    def load(self): return list(self.people)


class Scores:
    def __init__(self): self.values = []
    def load_scores(self): return list(self.values)


class Control:
    def load_tracking_eligible(self): return {1: True, 2: True, 3: True, 4: True}


class FollowUpRepositoryTests(unittest.TestCase):
    def test_baseline_preserves_note_and_second_refresh_is_noop(self):
        gateway, people, scores = Gateway(), People(), Scores()
        repo = FollowUpRepository(gateway, people, Scores(), Control())
        sessions = tuple(SessionRecord(i, str(i), str(i)) for i in range(1, 5))
        statuses = {i: "PROCESSED" for i in range(1, 5)}
        first = repo.refresh(sessions, statuses, official=True)
        self.assertEqual(first.status, "REPLACE")
        gateway.values[4][13] = "Contactar"
        second = repo.refresh(sessions, statuses, official=True)
        self.assertEqual(second.status, "NOOP")
        self.assertEqual(gateway.values[4][13], "Contactar")
        self.assertEqual(len({row[0] for row in gateway.values[4:]}), 1)

    def test_auto_mode_does_not_create_classification_sheet(self):
        gateway = Gateway()
        repo = FollowUpRepository(gateway, People(), Scores(), Control())
        result = repo.refresh((), {}, official=False)
        self.assertEqual(result.status, "NOOP")
        self.assertEqual(gateway.values, [])

    def test_structural_refresh_removes_stale_tail_rows(self):
        gateway, people = Gateway(), People()
        repo = FollowUpRepository(gateway, people, Scores(), Control())
        sessions = tuple(SessionRecord(i, str(i), str(i)) for i in range(1, 5))
        statuses = {i: "PROCESSED" for i in range(1, 5)}
        people.people.append(Participant("p2", "Bea"))
        repo.refresh(sessions, statuses, official=True)
        gateway.values.extend([["ghost"] + [""] * (len(FOLLOW_UP_HEADERS) - 1) for _ in range(2)])
        people.people.pop()
        result = repo.refresh(sessions, statuses, official=True)
        self.assertEqual(result.status, "REPLACE")
        self.assertEqual(len(gateway.values), 5)
