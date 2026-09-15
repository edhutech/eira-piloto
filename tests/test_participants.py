import unittest
from types import SimpleNamespace

from src.sync.participants import ParticipantResolver, ResolutionStatus, loose_name_key, strict_name_key


def event(name, identity_type="HUMAN", participant_id=None, email=None):
    return SimpleNamespace(
        participant_raw=name,
        identity_type=identity_type,
        participant_id=participant_id,
        participant_email=email,
    )


class ParticipantResolutionTests(unittest.TestCase):
    def test_new_participant_in_auto_mode(self):
        result = ParticipantResolver.auto().resolve_event(event("Alice Smith"))
        self.assertEqual(result.status, ResolutionStatus.RESOLVED)
        self.assertEqual(result.participant.nombre, "Alice Smith")
        self.assertEqual(result.participant.correo, "")
        self.assertEqual(result.participant.source, "auto")
        self.assertEqual(result.participant.status, "unverified")

    def test_same_participant_is_reused(self):
        resolver = ParticipantResolver.auto()
        first = resolver.resolve_event(event("Alice Smith"))
        second = resolver.resolve_event(event("Alice Smith"))
        self.assertEqual(first.participant.participant_id, second.participant.participant_id)
        self.assertEqual(len(resolver.participants), 1)

    def test_alias_resolves_imported_participant(self):
        resolver = ParticipantResolver.imported([{"participant_id": "p1", "nombre": "Alice Smith", "correo": "a@example.com", "aliases": ["Alicia"]}])
        result = resolver.resolve_event(event("Alicia"))
        self.assertEqual(result.participant.participant_id, "p1")

    def test_case_differences_resolve_by_normalized_name(self):
        resolver = ParticipantResolver.imported([{"participant_id": "p1", "nombre": "Alice Smith", "correo": "a@example.com"}])
        result = resolver.resolve_event(event("  alice SMITH "))
        self.assertEqual(result.status, ResolutionStatus.RESOLVED)
        self.assertEqual(result.participant.participant_id, "p1")

    def test_import_resolves_by_email(self):
        resolver = ParticipantResolver.imported([{"participant_id": "p1", "nombre": "Alice Smith", "correo": "a@example.com"}])
        result = resolver.resolve_event(event("Unknown label", email="a@example.com"))
        self.assertEqual(result.participant.participant_id, "p1")

    def test_import_resolves_by_name(self):
        resolver = ParticipantResolver.imported([{"participant_id": "p1", "nombre": "Alice Smith", "correo": "a@example.com"}])
        result = resolver.resolve_event(event("Alice Smith"))
        self.assertEqual(result.participant.participant_id, "p1")

    def test_multiple_candidates_need_review(self):
        resolver = ParticipantResolver.imported([
            {"participant_id": "p1", "nombre": "Alex One", "correo": "one@example.com", "aliases": ["Alex"]},
            {"participant_id": "p2", "nombre": "Alex Two", "correo": "two@example.com", "aliases": ["Alex"]},
        ])
        result = resolver.resolve_event(event("Alex"))
        self.assertEqual(result.status, ResolutionStatus.NEEDS_REVIEW)
        self.assertIsNone(result.participant)

    def test_system_event_is_ignored(self):
        resolver = ParticipantResolver.auto()
        result = resolver.resolve_event(event("Someone's Presentation", identity_type="SYSTEM"))
        self.assertEqual(result.status, ResolutionStatus.IGNORED)
        self.assertIsNone(result.participant)
        self.assertEqual(resolver.participants, [])

    def test_participant_id_is_stable_when_persisted(self):
        resolver = ParticipantResolver.auto()
        first = resolver.resolve_event(event("Alice Smith"))
        restored = ParticipantResolver.auto(resolver.to_records())
        second = restored.resolve_event(event("Alice Smith"))
        self.assertEqual(first.participant.participant_id, second.participant.participant_id)

    def test_name_or_alias_change_keeps_participant_id(self):
        resolver = ParticipantResolver.imported([{"participant_id": "p1", "nombre": "Alice Smith", "correo": "a@example.com", "aliases": ["Alice"]}])
        before = resolver.participants[0].participant_id
        records = resolver.to_records()
        records[0]["nombre"] = "Alicia Smith"
        records[0]["aliases"] = ["Alice", "Alicia"]
        restored = ParticipantResolver.imported(records)
        self.assertEqual(restored.participants[0].participant_id, before)
        self.assertEqual(restored.resolve_event(event("Alicia" )).participant_id, before)

    def test_default_role_is_participant(self):
        result = ParticipantResolver.auto().resolve_event(event("Alice Smith"))
        self.assertEqual(result.participant.role, "participant")

    def test_facilitator_role_is_preserved_when_configured_manually(self):
        resolver = ParticipantResolver.imported([{"participant_id": "p1", "nombre": "Facilitator", "correo": "f@example.com", "role": "facilitator"}])
        self.assertEqual(resolver.participants[0].role, "facilitator")
        self.assertEqual(resolver.resolve_event(event("Facilitator")).participant.role, "facilitator")

    def test_existing_participant_id_has_priority(self):
        resolver = ParticipantResolver.imported([
            {"participant_id": "p1", "nombre": "Alice", "correo": "a@example.com"},
            {"participant_id": "p2", "nombre": "Bob", "correo": "b@example.com"},
        ])
        result = resolver.resolve_event(event("Alice", participant_id="p2"))
        self.assertEqual(result.participant.participant_id, "p2")

    def test_strict_match_ignores_case_but_preserves_accents(self):
        self.assertEqual(strict_name_key("  ÁLICE   Smith "), strict_name_key("álice smith"))
        self.assertNotEqual(strict_name_key("José"), strict_name_key("Jose"))

    def test_loose_unique_match_is_secondary_signal(self):
        resolver = ParticipantResolver.imported([{"participant_id": "p1", "nombre": "José", "correo": "j@example.com"}])
        result = resolver.resolve_event(event("Jose"))
        self.assertEqual(result.status, ResolutionStatus.RESOLVED)
        self.assertEqual(result.participant_id, "p1")

    def test_loose_multiple_candidates_need_review(self):
        resolver = ParticipantResolver.imported([
            {"participant_id": "p1", "nombre": "José", "correo": "j1@example.com"},
            {"participant_id": "p2", "nombre": "Jóse", "correo": "j2@example.com"},
        ])
        result = resolver.resolve_event(event("JOSE"))
        self.assertEqual(result.status, ResolutionStatus.NEEDS_REVIEW)

    def test_official_exact_email_resolves_without_loose_matching(self):
        resolver = ParticipantResolver.official([{"participant_id": "p1", "nombre": "José", "correo": "j@example.com"}])
        result = resolver.resolve_event(event("Jose", email="j@example.com"))
        self.assertEqual(result.status, ResolutionStatus.RESOLVED)
        self.assertEqual(result.participant_id, "p1")

    def test_official_strict_name_and_alias_resolve(self):
        resolver = ParticipantResolver.official([
            {"participant_id": "p1", "nombre": "José", "correo": "j@example.com", "aliases": ["Pepe"]},
        ])
        self.assertEqual(resolver.resolve_event(event("José")).participant_id, "p1")
        self.assertEqual(resolver.resolve_event(event("Pepe")).participant_id, "p1")

    def test_official_unknown_and_loose_name_need_review(self):
        resolver = ParticipantResolver.official([{"participant_id": "p1", "nombre": "José", "correo": "j@example.com"}])
        self.assertEqual(resolver.resolve_event(event("Unknown")).status, ResolutionStatus.NEEDS_REVIEW)
        self.assertEqual(resolver.resolve_event(event("Jose")).status, ResolutionStatus.NEEDS_REVIEW)

    def test_official_ambiguous_match_needs_review(self):
        resolver = ParticipantResolver.official([
            {"participant_id": "p1", "nombre": "Alex One", "correo": "one@example.com", "aliases": ["Alex"]},
            {"participant_id": "p2", "nombre": "Alex Two", "correo": "two@example.com", "aliases": ["Alex"]},
        ])
        self.assertEqual(resolver.resolve_event(event("Alex")).status, ResolutionStatus.NEEDS_REVIEW)

    def test_existing_participants_are_not_merged_by_loose_match(self):
        resolver = ParticipantResolver.auto([
            {"participant_id": "p1", "nombre": "José", "correo": ""},
        ])
        result = resolver.resolve_event(event("Jose"))
        self.assertEqual(result.status, ResolutionStatus.RESOLVED)
        self.assertNotEqual(result.participant_id, "p1")
        self.assertEqual(len(resolver.participants), 2)

    def test_normalized_event_contract_still_has_no_participant_id(self):
        from src.sync.models import NormalizedEvent
        normalized = NormalizedEvent(1, "Alice", "voice", None, None, "raw", "text", "file", "line:1", "event")
        self.assertFalse(hasattr(normalized, "participant_id"))

    def test_loose_name_key_removes_diacritics(self):
        self.assertEqual(loose_name_key("José Álvarez"), loose_name_key("Jose Alvarez"))


if __name__ == "__main__":
    unittest.main()
