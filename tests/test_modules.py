import unittest
from decimal import Decimal

from participacion.application.external_data.models import AvailabilityStatus, CanonicalFact, SourceProvenance
from participacion.application.modules.attendance import AttendanceConfig, AttendanceInput, AttendanceModule
from participacion.application.modules.models import (
    ApplicabilityResult,
    ApplicabilityStatus,
    ExternalPair,
    ModuleIssueStatus,
    SourceCoverage,
)
from participacion.application.modules.participation import (
    ParticipationConfig,
    ParticipationModule,
    ParticipantSessionPair,
)
from participacion.application.modules.resolution import ResolutionResult, ResolutionStatus
from participacion.core.observation import ObservationStatus
from participacion.core.participants import ParticipantResolver
from participacion.core.scoring import ParticipantSessionScore


class ParticipantResolverFake:
    def __init__(self, mapping):
        self.mapping = mapping

    def resolve(self, external_id):
        result = self.mapping.get(external_id)
        return result or ResolutionResult(ResolutionStatus.NOT_FOUND)


class SessionResolverFake:
    def __init__(self, mapping):
        self.mapping = mapping

    def resolve(self, external_id):
        result = self.mapping.get(external_id)
        return result or ResolutionResult(ResolutionStatus.NOT_FOUND)


class ApplicabilityFake:
    def __init__(self, mapping):
        self.mapping = mapping

    def is_applicable(self, participant_id, session_id):
        return self.mapping.get((participant_id, session_id), ApplicabilityResult(ApplicabilityStatus.UNKNOWN))


def score(participant_id="p1", session_number=1, value=Decimal("1"), complete=True):
    return ParticipantSessionScore(session_number, participant_id, 1, int(value > 0), 0, 0, 0, value, complete)


def fact(participant="external-1", session="external-s1", metric="attendance-source", value=0.75):
    return CanonicalFact(
        participant, session, metric, value, AvailabilityStatus.AVAILABLE,
        SourceProvenance("source.xlsx", "sheet", 2, metric),
    )


class ParticipationModuleTests(unittest.TestCase):
    def setUp(self):
        self.sessions = SessionResolverFake({"1": ResolutionResult(ResolutionStatus.RESOLVED, "s1")})
        self.applicability = ApplicabilityFake({("p1", "s1"): ApplicabilityResult(ApplicabilityStatus.APPLICABLE)})
        self.module = ParticipationModule(
            self.sessions,
            self.applicability,
            ParticipationConfig(source_id="participation-test"),
        )

    def test_positive_score_is_observed(self):
        result = self.module.build_observations([score()])
        observation = result.observations[0]
        self.assertEqual((observation.dimension, observation.metric), ("participation", "participation_score"))
        self.assertEqual((observation.value, observation.status), (Decimal("1"), ObservationStatus.OBSERVED))

    def test_complete_zero_score_is_observed_zero(self):
        result = self.module.build_observations([score(value=Decimal("0"))])
        self.assertEqual(result.observations[0].status, ObservationStatus.OBSERVED)
        self.assertEqual(result.observations[0].value, Decimal("0"))

    def test_incomplete_score_is_incomplete_not_zero(self):
        result = self.module.build_observations([score(value=Decimal("0"), complete=False)])
        self.assertEqual(result.observations[0].status, ObservationStatus.INCOMPLETE)
        self.assertIsNone(result.observations[0].value)

    def test_provenance_is_preserved_as_neutral_reference(self):
        result = self.module.build_observations([score()])
        self.assertEqual(result.observations[0].provenance[0].source_id, "participation-test")

    def test_unresolvable_session_creates_issue_without_observation(self):
        result = ParticipationModule(
            SessionResolverFake({}), self.applicability, ParticipationConfig(source_id="test")
        ).build_observations([score()])
        self.assertEqual(result.observations, ())
        self.assertEqual(result.issues[0].status, ModuleIssueStatus.NEEDS_REVIEW)

    def test_unknown_applicability_creates_issue_without_observation(self):
        result = ParticipationModule(
            self.sessions,
            ApplicabilityFake({}),
            ParticipationConfig(source_id="test"),
        ).build_observations([score()])
        self.assertEqual(result.observations, ())
        self.assertEqual(result.issues[0].status, ModuleIssueStatus.NEEDS_REVIEW)

    def test_exhaustive_missing_expected_pair_is_no_data(self):
        result = ParticipationModule(
            self.sessions,
            self.applicability,
            ParticipationConfig(source_id="test", coverage=SourceCoverage.EXHAUSTIVE),
        ).build_observations([], expected_pairs=[ParticipantSessionPair("p1", "1")])
        self.assertEqual(result.observations[0].status, ObservationStatus.NO_DATA)
        self.assertIsNone(result.observations[0].value)

    def test_incomplete_status_wins_over_stale_score(self):
        result = self.module.build_observations(
            [score(value=Decimal("1"))], session_statuses={"1": "INCOMPLETE"}
        )
        self.assertNotEqual(result.observations[0].status, ObservationStatus.OBSERVED)
        self.assertIsNone(result.observations[0].value)

    def test_needs_review_status_wins_over_stale_score(self):
        result = self.module.build_observations(
            [score(value=Decimal("1"))], session_statuses={"1": "NEEDS_REVIEW"}
        )
        self.assertNotEqual(result.observations[0].status, ObservationStatus.OBSERVED)
        self.assertIsNone(result.observations[0].value)

    def test_processed_status_can_consume_score(self):
        result = self.module.build_observations(
            [score(value=Decimal("1"))], session_statuses={"1": "PROCESSED"}
        )
        self.assertEqual(result.observations[0].status, ObservationStatus.OBSERVED)


class AttendanceModuleTests(unittest.TestCase):
    def setUp(self):
        self.module = AttendanceModule(
            ParticipantResolverFake({"external-1": ResolutionResult(ResolutionStatus.RESOLVED, "p1")}),
            SessionResolverFake({"external-s1": ResolutionResult(ResolutionStatus.RESOLVED, "s1")}),
            ApplicabilityFake({("p1", "s1"): ApplicabilityResult(ApplicabilityStatus.APPLICABLE)}),
            AttendanceConfig(metric_sources={"attendance_ratio": "attendance-source"}),
        )

    def test_canonical_fact_becomes_observation(self):
        result = self.module.build_observations(AttendanceInput((fact(),)))
        self.assertEqual(result.observations[0].participant_id, "p1")
        self.assertEqual(result.observations[0].session_id, "s1")
        self.assertEqual(result.observations[0].value, 0.75)
        self.assertEqual(result.observations[0].status, ObservationStatus.OBSERVED)

    def test_explicit_metric_selection_does_not_choose_other_source_metric(self):
        module = AttendanceModule(
            self.module.participant_resolver,
            self.module.session_resolver,
            self.module.applicability_resolver,
            AttendanceConfig(metric_sources={"attendance_ratio": "p_attendance_pct_unified"}),
        )
        result = module.build_observations(AttendanceInput((
            fact(metric="p_class_attendance_pct", value=0.2),
            fact(metric="p_attendance_pct_unified", value=0.8),
        )))
        self.assertEqual(result.observations[0].value, 0.8)

    def test_optional_metric_missing_is_no_data(self):
        module = AttendanceModule(
            self.module.participant_resolver,
            self.module.session_resolver,
            self.module.applicability_resolver,
            AttendanceConfig(metric_sources={"attendance_ratio": "attendance-source", "duration_minutes": "duration-source"}),
        )
        result = module.build_observations(AttendanceInput((fact(),)))
        by_metric = {item.metric: item for item in result.observations}
        self.assertEqual(by_metric["duration_minutes"].status, ObservationStatus.NO_DATA)

    def test_missing_row_is_not_absence_by_default(self):
        result = self.module.build_observations(AttendanceInput((), coverage=SourceCoverage.UNKNOWN,
                                                                  expected_pairs=(ExternalPair("external-1", "external-s1"),)))
        self.assertEqual(result.observations, ())

    def test_exhaustive_missing_row_is_no_data_not_zero(self):
        result = self.module.build_observations(AttendanceInput((), coverage=SourceCoverage.EXHAUSTIVE,
                                                                  expected_pairs=(ExternalPair("external-1", "external-s1"),)))
        self.assertEqual(result.observations[0].status, ObservationStatus.NO_DATA)
        self.assertIsNone(result.observations[0].value)

    def test_not_applicable_has_no_zero_value(self):
        module = AttendanceModule(
            self.module.participant_resolver,
            self.module.session_resolver,
            ApplicabilityFake({("p1", "s1"): ApplicabilityResult(ApplicabilityStatus.NOT_APPLICABLE)}),
            AttendanceConfig(metric_sources={"attendance_ratio": "attendance-source"}),
        )
        result = module.build_observations(AttendanceInput((fact(),)))
        self.assertEqual(result.observations[0].status, ObservationStatus.NOT_APPLICABLE)
        self.assertIsNone(result.observations[0].value)

    def test_unknown_applicability_is_needs_review(self):
        module = AttendanceModule(
            self.module.participant_resolver,
            self.module.session_resolver,
            ApplicabilityFake({}),
            AttendanceConfig(metric_sources={"attendance_ratio": "attendance-source"}),
        )
        result = module.build_observations(AttendanceInput((fact(),)))
        self.assertEqual(result.observations, ())
        self.assertEqual(result.issues[0].status, ModuleIssueStatus.NEEDS_REVIEW)

    def test_ambiguous_identity_is_needs_review(self):
        module = AttendanceModule(
            ParticipantResolverFake({"external-1": ResolutionResult(ResolutionStatus.NEEDS_REVIEW)}),
            self.module.session_resolver, self.module.applicability_resolver,
            AttendanceConfig(metric_sources={"attendance_ratio": "attendance-source"}),
        )
        result = module.build_observations(AttendanceInput((fact(),)))
        self.assertEqual(result.issues[0].status, ModuleIssueStatus.NEEDS_REVIEW)

    def test_unresolved_session_is_needs_review(self):
        module = AttendanceModule(
            self.module.participant_resolver,
            SessionResolverFake({}), self.module.applicability_resolver,
            AttendanceConfig(metric_sources={"attendance_ratio": "attendance-source"}),
        )
        result = module.build_observations(AttendanceInput((fact(),)))
        self.assertEqual(result.issues[0].status, ModuleIssueStatus.NEEDS_REVIEW)

    def test_provenance_is_conserved(self):
        result = self.module.build_observations(AttendanceInput((fact(),)))
        provenance = result.observations[0].provenance[0]
        self.assertEqual((provenance.source_id, provenance.locator), ("source.xlsx", "sheet:2:attendance-source"))

    def test_invalid_duplicate_facts_are_not_merged(self):
        result = self.module.build_observations(AttendanceInput((fact(), fact(value=0.8))))
        self.assertEqual(result.observations, ())
        self.assertEqual(result.issues[0].status, ModuleIssueStatus.INVALID)


class ExistingResolverAdapterTests(unittest.TestCase):
    def test_existing_participant_resolver_can_be_used_strictly(self):
        resolver = ParticipantResolver.official([
            {"participant_id": "p1", "nombre": "Ana", "correo": "ana@example.com"}
        ])
        from participacion.application.modules.resolution import ParticipantResolverAdapter
        result = ParticipantResolverAdapter(resolver, match_email=True).resolve("ana@example.com")
        self.assertEqual((result.status, result.internal_id), (ResolutionStatus.RESOLVED, "p1"))


if __name__ == "__main__":
    unittest.main()
