from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal

from participacion.application.external_data.models import TabularRow, TabularTable
from participacion.application.pilot.config import PilotConfig
from participacion.application.pilot.results import HistoricalSnapshot, PilotResult
from participacion.application.pilot.retrospective import OutcomeRecord, RetrospectiveEvaluator
from participacion.application.pilot.runner import PilotRunner, PilotSources
from participacion.application.modules.models import ApplicabilityResult, ApplicabilityStatus
from participacion.application.modules.resolution import ParticipantResolverAdapter
from participacion.core.alerts import AlertEvaluationStatus, AlertLevel
from participacion.core.participants import ParticipantResolver
from participacion.core.scoring import ParticipantSessionScore
from participacion.core.signals import SignalSet


class KnownApplicability:
    def is_applicable(self, participant_id: str, session_id: str) -> ApplicabilityResult:
        return ApplicabilityResult(ApplicabilityStatus.APPLICABLE)


class PilotTests(unittest.TestCase):
    def _config(self) -> PilotConfig:
        return PilotConfig.from_dict({
            "program": {"program_id": "program-1", "program_name": "Synthetic", "sheet_id": "sheet-1"},
            "participation": {"source_id": "google:sheet-1", "coverage": "UNKNOWN"},
            "attendance": {"source": {"type": "xlsx", "file": "local.xlsx", "sheet": "participants"},
                           "mapping": {"participant_external_id": {"column": "external_person", "type": "string", "required": True},
                                       "session_external_id": {"column": "external_session", "type": "string", "required": True},
                                       "metrics": {"attendance_ratio_a": {"column": "attendance_a", "type": "number"}}}},
            "session_mapping": {"S1": {"session_id": "session-1", "session_order": 1},
                                "S2": {"session_id": "session-2", "session_order": 2}},
            "longitudinal": {"minimum_observations": 1, "recent_window_size": 1, "trend_threshold": 0},
            "signals": {"version": "pilot.v1", "participation_silence_streak": {"enabled": True, "minimum_streak": 2}},
            "alerts": {"version": "pilot.v1", "silence_level": "OBSERVAR"},
        })

    def test_runner_combines_modules_and_preserves_provenance(self) -> None:
        table = TabularTable("local.xlsx", "participants", ("external_person", "external_session", "attendance_a"), (
            TabularRow(2, {"external_person": "external-1", "external_session": "S1", "attendance_a": 80}),
            TabularRow(3, {"external_person": "external-1", "external_session": "S2", "attendance_a": 70}),
        ))
        scores = (
            ParticipantSessionScore(1, "participant-1", 0, 0, 0, 0, 0, Decimal("0"), True),
            ParticipantSessionScore(2, "participant-1", 1, 1, 0, 1, 0, Decimal("1"), True),
        )
        resolver = ParticipantResolverAdapter(ParticipantResolver.official([{
            "participant_id": "participant-1", "nombre": "Synthetic Person", "correo": "external-1",
        }]), match_email=True)
        sources = PilotSources(
            participant_scores=lambda: scores,
            attendance_table=lambda: table,
            participant_resolver=resolver,
        )
        result = PilotRunner(self._config(), sources).run()
        self.assertEqual(result.snapshot_count, 2)
        self.assertEqual(len(result.observations), 4)
        self.assertTrue(all(item.provenance for item in result.observations))
        first = result.snapshots[0]
        self.assertEqual(first.as_of_order, 1)
        self.assertEqual(first.alerts[0].evaluation_status, AlertEvaluationStatus.EVALUATED)
        self.assertEqual(first.alerts[0].level, AlertLevel.NORMAL)
        second = result.snapshots[1]
        self.assertEqual(second.as_of_session_id, "session-2")

    def test_zero_observed_scores_generate_experimental_silence_alert_after_two_sessions(self) -> None:
        scores = (
            ParticipantSessionScore(1, "participant-1", 0, 0, 0, 0, 0, Decimal("0"), True),
            ParticipantSessionScore(2, "participant-1", 0, 0, 0, 0, 0, Decimal("0"), True),
        )
        table = TabularTable("local.xlsx", "participants", ("external_person", "external_session", "attendance_a"), (
            TabularRow(2, {"external_person": "external-1", "external_session": "S1", "attendance_a": 80}),
            TabularRow(3, {"external_person": "external-1", "external_session": "S2", "attendance_a": 70}),
        ))
        resolver = ParticipantResolverAdapter(ParticipantResolver.official([{
            "participant_id": "participant-1", "nombre": "Synthetic Person", "correo": "external-1",
        }]), match_email=True)
        result = PilotRunner(self._config(), PilotSources(lambda: scores, lambda: table, resolver)).run()
        self.assertEqual(result.snapshots[1].signal_set.signals[0].signal_type, "participation_silence_streak")
        self.assertEqual(result.snapshots[1].alerts[0].level, AlertLevel.OBSERVAR)

    def test_missing_scores_in_processed_sessions_generate_silence_alert(self) -> None:
        raw = self._config().to_dict()
        raw.pop("attendance")
        config = PilotConfig.from_dict(raw)
        resolver = ParticipantResolverAdapter(ParticipantResolver.official([{
            "participant_id": "participant-1", "nombre": "Synthetic Person", "correo": "person@example.test",
        }]), match_email=True)
        result = PilotRunner(
            config,
            PilotSources(
                participant_scores=lambda: (),
                attendance_table=None,
                participant_resolver=resolver,
                participant_ids=lambda: ("participant-1",),
                participation_statuses=lambda: {"1": "PROCESSED", "2": "PROCESSED"},
            ),
        ).run()
        self.assertEqual(result.snapshots[1].signal_set.signals[0].signal_type, "participation_silence_streak")
        self.assertEqual(result.snapshots[1].alerts[0].level, AlertLevel.OBSERVAR)

    def test_incomplete_current_session_is_insufficient_not_normal(self) -> None:
        raw = self._config().to_dict()
        raw.pop("attendance")
        config = PilotConfig.from_dict(raw)
        scores = (ParticipantSessionScore(1, "participant-1", 1, 1, 0, 0, 0, Decimal("1"), True),)
        resolver = ParticipantResolverAdapter(ParticipantResolver.official([{
            "participant_id": "participant-1", "nombre": "Synthetic Person", "correo": "person@example.test",
        }]), match_email=True)
        result = PilotRunner(
            config,
            PilotSources(
                participant_scores=lambda: scores,
                attendance_table=None,
                participant_resolver=resolver,
                participant_ids=lambda: ("participant-1",),
                participation_statuses=lambda: {"1": "PROCESSED", "2": "INCOMPLETE"},
            ),
        ).run()
        self.assertEqual(result.snapshots[1].signal_set.signals, ())
        self.assertEqual(result.snapshots[1].alerts[0].evaluation_status, AlertEvaluationStatus.INSUFFICIENT_DATA)
        self.assertIsNone(result.snapshots[1].alerts[0].level)

    def test_invalid_silence_level_is_rejected(self) -> None:
        raw = self._config().to_dict()
        raw["alerts"]["silence_level"] = "CRITICO"
        with self.assertRaisesRegex(ValueError, "solo puede ser OBSERVAR"):
            PilotConfig.from_dict(raw)

    def test_historical_snapshot_does_not_use_future_observations(self) -> None:
        config = self._config()
        scores = (
            ParticipantSessionScore(1, "participant-1", 0, 0, 0, 0, 0, Decimal("0"), True),
            ParticipantSessionScore(2, "participant-1", 0, 0, 0, 0, 0, Decimal("0"), True),
        )
        table = TabularTable("local.xlsx", "participants", ("external_person", "external_session", "attendance_a"), (
            TabularRow(2, {"external_person": "external-1", "external_session": "S1", "attendance_a": 80}),
            TabularRow(3, {"external_person": "external-1", "external_session": "S2", "attendance_a": 70}),
        ))
        resolver = ParticipantResolverAdapter(ParticipantResolver.official([{
            "participant_id": "participant-1", "nombre": "Synthetic Person", "correo": "external-1",
        }]), match_email=True)
        result = PilotRunner(config, PilotSources(lambda: scores, lambda: table, resolver)).run()
        self.assertEqual(len(result.snapshots[0].observations), 2)
        self.assertEqual(len(result.snapshots[1].observations), 4)
        self.assertEqual(result.snapshots[0].as_of_order, 1)

    def test_retrospective_only_considers_snapshots_before_outcome(self) -> None:
        snapshots = (
            HistoricalSnapshot("S1", 1, (), (), SignalSet((), ()), ()),
            HistoricalSnapshot("S2", 2, (), (), SignalSet((), ()), ()),
        )
        result = PilotResult("program-1", snapshots, (), ())
        report = RetrospectiveEvaluator().evaluate(result, (
            OutcomeRecord("participant-1", datetime(2026, 9, 3), "BAJA", "outcome:row:2"),
        ), {"S1": datetime(2026, 8, 25), "S2": datetime(2026, 9, 3)})
        self.assertEqual(report.outcomes_evaluable, 1)
        self.assertEqual(report.cases_with_prior_signal, 0)

    def test_config_rejects_unknown_session_mapping(self) -> None:
        raw = self._config().to_dict()
        raw["session_mapping"]["S3"] = {"session_id": "session-3"}
        with self.assertRaises(ValueError):
            PilotConfig.from_dict(raw)


if __name__ == "__main__":
    unittest.main()
