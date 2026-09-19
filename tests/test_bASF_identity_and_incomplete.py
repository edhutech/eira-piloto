import unittest
from types import SimpleNamespace

from participacion.application.init_program import build_plan
from participacion.application.modules.models import ParticipantSessionPair
from participacion.application.modules.participation import ParticipationConfig, ParticipationModule
from participacion.application.modules.resolution import (
    AlwaysApplicable,
    EventIdentityResolver,
    KnownExternalIdentity,
    MappingSessionResolver,
    ResolutionStatus,
)
from participacion.application.roster import roster_records_from_rows, roster_records_to_participants

from participacion.core.participants import Participant, ParticipantResolver, ResolutionStatus as CoreResolutionStatus
from participacion.core.observation import ObservationStatus



class IdentityAndIncompleteTests(unittest.TestCase):
    def participant(self, participant_id="p1"):
        return Participant(participant_id, "Nombre Oficial", "official@example.test", ["Nombre Alias", "Nombre Alias 2"])

    def test_legacy_roster_without_aliases_is_unchanged(self):
        records = roster_records_from_rows([["nombre", "correo"], ["Persona", "p@example.test"]])
        self.assertEqual(records[0].aliases, ())
        self.assertEqual(roster_records_to_participants(records, "test")[0]["aliases"], "")

    def test_aliases_resolve_to_same_official_participant(self):
        resolver = ParticipantResolver.official([{
            "participant_id": "p1", "nombre": "Nombre Oficial", "correo": "p@example.test",
            "aliases": "Nombre Alias\nNombre Alias 2\nNombre Alias",
        }])
        for observed in ("Nombre Alias", " Nombre   Alias 2 "):
            self.assertEqual(resolver.resolve(observed).participant_id, "p1")

    def test_alias_duplicate_is_normalized(self):
        records = roster_records_from_rows([
            ["participant_id", "nombre", "correo", "aliases"],
            ["p1", "Persona", "p@example.test", "Alias\n alias  \nOtro"],
        ])
        self.assertEqual(records[0].aliases, ("Alias", "Otro"))

    def test_ambiguous_alias_requires_review(self):
        resolver = ParticipantResolver.official([
            {"participant_id": "p1", "nombre": "Uno", "correo": "1@example.test", "aliases": ["Alias"]},
            {"participant_id": "p2", "nombre": "Dos", "correo": "2@example.test", "aliases": ["Alias"]},
        ])
        self.assertEqual(resolver.resolve("Alias").status, CoreResolutionStatus.NEEDS_REVIEW)

    def test_known_external_identity_is_ignored_with_provenance(self):
        policy = EventIdentityResolver(
            ParticipantResolver.official([{"participant_id": "p1", "nombre": "Oficial", "correo": "p@example.test"}]),
            {"externo conocido": KnownExternalIdentity("Externo conocido", "outside roster", "gOS_class_participants")},
        )
        event = SimpleNamespace(identity_type="HUMAN", participant_raw="Externo  conocido")
        result = policy.resolve_event(event)
        self.assertEqual(result.status, ResolutionStatus.IGNORED)
        self.assertEqual(getattr(result, "provenance"), "gOS_class_participants")

    def test_unknown_identity_requires_review(self):
        resolver = ParticipantResolver.official([{"participant_id": "p1", "nombre": "Oficial", "correo": "p@example.test"}])
        self.assertEqual(resolver.resolve("Desconocido").status, CoreResolutionStatus.NEEDS_REVIEW)

    def test_evidence_sources_plan_has_no_session_folders(self):
        plan = build_plan(
            program_name="P", folder_id="root", folder_url="url", session_count=1,
            participant_mode="auto", imported_participants=[], existing_children={}, existing_sheet_id=None,
            evidence_sessions=[{"session_number": 1, "session_id": "S01", "session_name": "Sesión 1",
                                "evidence_sources": [{"provider": "google", "kind": "artifact", "ref": "a",
                                                      "evidence_type": "chat"}]}],
        )
        self.assertEqual(plan.source_mode, "evidence_sources")
        self.assertEqual(plan.session_folders, [])
        self.assertNotIn("source_ref", plan.evidence_sessions[0])

    def test_s06_explicit_incomplete_is_not_zero_or_no_data(self):
        module = ParticipationModule(MappingSessionResolver({"6": "S06"}), AlwaysApplicable(), ParticipationConfig("test"))
        result = module.build_observations(
            [], expected_pairs=[ParticipantSessionPair("p1", "6")], session_statuses={"6": "INCOMPLETE"}
        )
        self.assertEqual({item.status for item in result.observations}, {ObservationStatus.INCOMPLETE})
        self.assertTrue(all(item.value is None for item in result.observations))

    def test_missing_score_without_explicit_status_is_not_incomplete(self):
        module = ParticipationModule(MappingSessionResolver({"6": "S06"}), AlwaysApplicable(), ParticipationConfig("test"))
        result = module.build_observations([], expected_pairs=[ParticipantSessionPair("p1", "6")])
        self.assertEqual(result.observations, ())


if __name__ == "__main__":
    unittest.main()
