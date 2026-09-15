import unittest

from src.sync.participants import Participant
from src.sync.roster import (RosterAction, RosterImporter, RosterRecord,
                              _stable_official_id)


class FakeParticipantRepository:
    def __init__(self, people):
        self.people = list(people)
        self.upserted = []
        self.updated = {}

    def load(self):
        return list(self.people)

    def upsert(self, people):
        self.upserted.extend(people)
        self.people.extend(people)

    def update_fields(self, updates):
        self.updated.update(updates)


class RosterTests(unittest.TestCase):
    def test_email_then_name_then_alias_and_stable_new_id(self):
        repo = FakeParticipantRepository([Participant("p1", "Ana", "ana@example.com", ["Anita"])])
        importer = RosterImporter(repo)
        plan = importer.dry_run([
            RosterRecord("Ana Nueva", "ana@example.com"),
            RosterRecord("Anita", "other@example.com"),
            RosterRecord("Bea", "bea@example.com"),
        ])
        self.assertEqual([item.action for item in plan.items], [RosterAction.UPDATE, RosterAction.UPDATE, RosterAction.CREATE])
        self.assertEqual(plan.items[2].participant_id, _stable_official_id(plan.items[2].source))
        importer.apply(plan)
        self.assertEqual(len(repo.people), 2)
        self.assertEqual(len(repo.updated), 1)

    def test_ambiguous_match_blocks_apply(self):
        repo = FakeParticipantRepository([Participant("p1", "Ana", ""), Participant("p2", "Ana", "")])
        plan = RosterImporter(repo).dry_run([RosterRecord("Ana", "ana@example.com")])
        self.assertEqual(plan.items[0].action, RosterAction.NEEDS_REVIEW)
        with self.assertRaises(ValueError):
            RosterImporter(repo).apply(plan)

    def test_apply_is_non_destructive(self):
        repo = FakeParticipantRepository([Participant("f1", "Facilitador", "f@example.com", role="facilitator")])
        plan = RosterImporter(repo).dry_run([RosterRecord("Estudiante", "e@example.com")])
        RosterImporter(repo).apply(plan)
        self.assertEqual(repo.people[0].role, "facilitator")
        self.assertEqual(repo.people[0].participant_id, "f1")
