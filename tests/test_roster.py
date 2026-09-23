import unittest

from participacion.core.participants import Participant
from participacion.application.roster import (RosterAction, RosterImporter, RosterRecord,
                              _stable_official_id, roster_records_from_rows,
                              roster_records_to_participants)


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
    def test_explicit_participant_id_is_preserved_in_import_records(self):
        records = roster_records_from_rows([
            ["participant_id", "nombre", "correo", "role"],
            ["basf_p_1", "Ana", "ana@example.com", "participant"],
        ])
        self.assertEqual(roster_records_to_participants(records, "test")[0]["participant_id"], "basf_p_1")

    def test_explicit_participant_ids_must_be_complete_and_unique(self):
        with self.assertRaises(ValueError):
            roster_records_from_rows([["participant_id", "nombre", "correo"], ["", "Ana", "a@example.com"]])
        with self.assertRaises(ValueError):
            roster_records_from_rows([["participant_id", "nombre", "correo"],
                                      ["p1", "Ana", "a@example.com"], ["p1", "Bea", "b@example.com"]])

    def test_old_roster_without_participant_id_keeps_current_behavior(self):
        records = roster_records_from_rows([["nombre", "correo"], ["Ana", "a@example.com"]])
        self.assertEqual(roster_records_to_participants(records, "test")[0]["participant_id"], "")

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

    def test_duplicate_roster_email_blocks_apply(self):
        records = [RosterRecord("Ana", "ana@example.com"), RosterRecord("Bea", "ana@example.com")]
        plan = RosterImporter(FakeParticipantRepository([])).dry_run(records)
        self.assertEqual([item.action for item in plan.items], [RosterAction.NEEDS_REVIEW, RosterAction.NEEDS_REVIEW])

    def test_existing_collision_with_new_official_id_blocks_apply(self):
        record = RosterRecord("Nueva", "new@example.com")
        collision = Participant(_stable_official_id(record), "Otra", "other@example.com")
        plan = RosterImporter(FakeParticipantRepository([collision])).dry_run([record])
        self.assertEqual(plan.items[0].action, RosterAction.NEEDS_REVIEW)

    def test_roster_role_is_ignored_for_existing_and_new_people(self):
        repo = FakeParticipantRepository([Participant("p1", "Ana", "ana@example.com", role="facilitator")])
        plan = RosterImporter(repo).dry_run([RosterRecord("Ana", "ana@example.com", role="participant")])
        RosterImporter(repo).apply(plan)
        self.assertEqual(repo.people[0].role, "facilitator")
        created_repo = FakeParticipantRepository([])
        created_plan = RosterImporter(created_repo).dry_run([RosterRecord("Bea", "bea@example.com", role="other")])
        RosterImporter(created_repo).apply(created_plan)
        self.assertEqual(created_repo.people[0].role, "participant")

    def test_stable_participant_id_matches_across_email_change(self):
        person = Participant("stable-1", "Synthetic Person", "old@example.test", ["Old alias"],
                             enrollment_status="inactive", start_session=2, end_session=5)
        repo = FakeParticipantRepository([person])
        record = RosterRecord("Synthetic Person", "new@example.test", participant_id="stable-1")
        from participacion.cli.cloud import _preserve_unspecified_roster_fields
        preserved = _preserve_unspecified_roster_fields(
            [record], repo.load(), {"participant_id", "nombre", "correo"})
        plan = RosterImporter(repo).dry_run(preserved)
        RosterImporter(repo).apply(plan)
        self.assertEqual(plan.items[0].action, RosterAction.UPDATE)
        self.assertEqual(repo.updated["stable-1"]["correo"], "new@example.test")
        self.assertEqual(repo.updated["stable-1"]["aliases"], "Old alias")
        self.assertEqual(repo.updated["stable-1"]["start_session"], 2)
        self.assertEqual(repo.updated["stable-1"]["end_session"], 5)
