import unittest

from participacion.application.alert_engine import AlertEngine, AlertEngineConfig, AlertRule
from participacion.application.signal_engine import (
    DeclineSignalRule,
    RecoverySignalRule,
    SignalEngine,
    SignalEngineConfig,
    StreakSignalRule,
)
from participacion.core.alerts import AlertEvaluationStatus, AlertLevel
from participacion.core.longitudinal import LongitudinalConfig, StreakRule, analyze_longitudinal
from participacion.core.observation import Observation, ObservationStatus



ORDER = {"s1": 1, "s2": 2, "s3": 3, "s4": 4, "s5": 5, "s6": 6}


def obs(session, value, *, participant="p1", dimension="participation", metric="participation_score", status=ObservationStatus.OBSERVED):
    return Observation(participant, session, dimension, metric, value, status)


def analysis(observations, *, window=2, minimum=3, streak_rules=()):
    return analyze_longitudinal(
        observations,
        ORDER,
        LongitudinalConfig(minimum_observations=minimum, recent_window_size=window, streak_rules=streak_rules),
    )


class SignalEngineTests(unittest.TestCase):
    def test_participation_decline_signal_has_explanation_and_version(self):
        analyses = analysis([obs("s1", 3), obs("s2", 3), obs("s3", 1), obs("s4", 1)])
        result = SignalEngine(SignalEngineConfig(
            decline_rules=(DeclineSignalRule("participation_decline", "participation", "participation_score", 1.0, 4, "decreasing", "participation.v1"),),
        )).generate(analyses)
        self.assertEqual([item.signal_type for item in result.signals], ["participation_decline"])
        self.assertEqual(result.signals[0].rule_version, "participation.v1")
        self.assertEqual(result.signals[0].evidence["delta"], -2.0)

    def test_decline_threshold_is_configurable(self):
        analyses = analysis([obs("s1", 3), obs("s2", 3), obs("s3", 2), obs("s4", 2)])
        low = SignalEngine(SignalEngineConfig(
            decline_rules=(DeclineSignalRule("decline", "participation", "participation_score", 0.5, 4, None, "v1"),),
        )).generate(analyses)
        high = SignalEngine(SignalEngineConfig(
            decline_rules=(DeclineSignalRule("decline", "participation", "participation_score", 2.0, 4, None, "v1"),),
        )).generate(analyses)
        self.assertEqual(len(low.signals), 1)
        self.assertEqual(len(high.signals), 0)

    def test_insufficient_data_does_not_generate_signal(self):
        result = SignalEngine(SignalEngineConfig(
            decline_rules=(DeclineSignalRule("decline", "participation", "participation_score", 0.1, 4, None, "v1"),),
        )).generate(analysis([obs("s1", 3), obs("s2", 1)], minimum=4))
        self.assertEqual(result.signals, ())
        self.assertFalse(result.evaluations[0].sufficient_data)

    def test_silence_streak_requires_observed_zero(self):
        rule = StreakRule("participation_zero", lambda item: item.status is ObservationStatus.OBSERVED and item.value == 0)
        analyses = analysis([obs("s1", 1), obs("s2", 0), obs("s3", 0), obs("s4", 0)], streak_rules=(rule,))
        result = SignalEngine(SignalEngineConfig(
            streak_rules=(StreakSignalRule("participation_silence_streak", "participation", "participation_score", "participation_zero", 3, "v1"),),
        )).generate(analyses)
        self.assertEqual(result.signals[0].signal_type, "participation_silence_streak")

    def test_no_data_does_not_continue_silence_streak(self):
        rule = StreakRule("participation_zero", lambda item: item.status is ObservationStatus.OBSERVED and item.value == 0)
        analyses = analysis([obs("s1", 0), obs("s2", 0), obs("s3", None, status=ObservationStatus.NO_DATA), obs("s4", 0)], streak_rules=(rule,))
        result = SignalEngine(SignalEngineConfig(
            streak_rules=(StreakSignalRule("silence", "participation", "participation_score", "participation_zero", 3, "v1"),),
        )).generate(analyses)
        self.assertEqual(result.signals, ())

    def test_attendance_absence_streak_requires_explicit_predicate(self):
        rule = StreakRule("configured_absence", lambda item: (
            item.status is ObservationStatus.OBSERVED and item.value is not None and item.value <= 0.1
        ))
        analyses = analysis([
            obs("s1", 0.1, dimension="attendance", metric="attendance_ratio"),
            obs("s2", 0.1, dimension="attendance", metric="attendance_ratio"),
            obs("s3", 0.1, dimension="attendance", metric="attendance_ratio"),
        ], minimum=3, streak_rules=(rule,))
        result = SignalEngine(SignalEngineConfig(
            streak_rules=(StreakSignalRule("attendance_absence_streak", "attendance", "attendance_ratio", "configured_absence", 3, "attendance.v1"),),
        )).generate(analyses)
        self.assertEqual(result.signals[0].signal_type, "attendance_absence_streak")

    def test_incomplete_and_not_applicable_do_not_activate_absence_streak(self):
        rule = StreakRule("configured_absence", lambda item: (
            item.status is ObservationStatus.OBSERVED and item.value is not None and item.value <= 0.1
        ))
        analyses = analysis([
            obs("s1", 0.1, dimension="attendance", metric="attendance_ratio"),
            obs("s2", None, dimension="attendance", metric="attendance_ratio", status=ObservationStatus.INCOMPLETE),
            obs("s3", None, dimension="attendance", metric="attendance_ratio", status=ObservationStatus.NOT_APPLICABLE),
        ], minimum=1, streak_rules=(rule,))
        result = SignalEngine(SignalEngineConfig(
            streak_rules=(StreakSignalRule("absence", "attendance", "attendance_ratio", "configured_absence", 2, "v1"),),
        )).generate(analyses)
        self.assertEqual(result.signals, ())

    def test_attendance_decline_uses_selected_metric(self):
        analyses = analysis([
            obs("s1", 0.9, dimension="attendance", metric="attendance_ratio"),
            obs("s2", 0.9, dimension="attendance", metric="attendance_ratio"),
            obs("s3", 0.4, dimension="attendance", metric="attendance_ratio"),
            obs("s4", 0.4, dimension="attendance", metric="attendance_ratio"),
        ])
        result = SignalEngine(SignalEngineConfig(
            decline_rules=(DeclineSignalRule("attendance_decline", "attendance", "attendance_ratio", 0.2, 4, "decreasing", "attendance.v1"),),
        )).generate(analyses)
        self.assertEqual(result.signals[0].dimensions, ("attendance",))

    def test_recovery_requires_prior_deterioration(self):
        analyses = analysis([
            obs("s1", 3), obs("s2", 3), obs("s3", 1), obs("s4", 1), obs("s5", 3), obs("s6", 3),
        ], window=2, minimum=6)
        result = SignalEngine(SignalEngineConfig(
            recovery_rules=(RecoverySignalRule("recovery", "participation", "participation_score", 0.5, 1.0, 6, "recovery.v1"),),
        )).generate(analyses)
        self.assertEqual(result.signals[0].signal_type, "recovery")
        self.assertIn("previous_recent_average", result.signals[0].evidence)

    def test_recovery_not_generated_for_simple_first_improvement(self):
        analyses = analysis([obs("s1", 1), obs("s2", 2), obs("s3", 3), obs("s4", 4)], window=2, minimum=4)
        result = SignalEngine(SignalEngineConfig(
            recovery_rules=(RecoverySignalRule("recovery", "participation", "participation_score", 0.5, 1.0, 4, "v1"),),
        )).generate(analyses)
        self.assertEqual(result.signals, ())


class AlertEngineTests(unittest.TestCase):
    def test_single_decline_produces_observar(self):
        analyses = analysis([obs("s1", 3), obs("s2", 3), obs("s3", 1), obs("s4", 1)])
        signal_set = SignalEngine(SignalEngineConfig(
            decline_rules=(DeclineSignalRule("participation_decline", "participation", "participation_score", 1, 4, None, "signal.v1"),),
        )).generate(analyses)
        alerts = AlertEngine(AlertEngineConfig(
            rules=(AlertRule(("participation_decline",), AlertLevel.OBSERVAR, "alert.v1"),),
            required_evaluations=(("participation", "participation_score"),),
        )).evaluate(signal_set)
        self.assertEqual(alerts[0].level, AlertLevel.OBSERVAR)
        self.assertEqual(alerts[0].evaluation_status, AlertEvaluationStatus.EVALUATED)

    def test_signal_can_alert_when_another_dimension_is_insufficient(self):
        participation = analysis([obs("s1", 3), obs("s2", 3), obs("s3", 1), obs("s4", 1)])
        attendance = analysis([
            obs("s1", 0.8, dimension="attendance", metric="attendance_ratio"),
            obs("s2", 0.8, dimension="attendance", metric="attendance_ratio"),
        ], minimum=4)
        signal_set = SignalEngine(SignalEngineConfig(
            decline_rules=(DeclineSignalRule("participation_decline", "participation", "participation_score", 1, 4, None, "p.v1"),),
        )).generate(participation + attendance)
        alerts = AlertEngine(AlertEngineConfig(
            rules=(AlertRule(("participation_decline",), AlertLevel.OBSERVAR, "alert.v1"),),
            required_evaluations=(("participation", "participation_score"), ("attendance", "attendance_ratio")),
        )).evaluate(signal_set)
        self.assertEqual(alerts[0].level, AlertLevel.OBSERVAR)

    def test_two_declines_produce_critical(self):
        analyses = analysis([obs("s1", 3), obs("s2", 3), obs("s3", 1), obs("s4", 1)]) + analysis([
            obs("s1", 0.9, dimension="attendance", metric="attendance_ratio"),
            obs("s2", 0.9, dimension="attendance", metric="attendance_ratio"),
            obs("s3", 0.4, dimension="attendance", metric="attendance_ratio"),
            obs("s4", 0.4, dimension="attendance", metric="attendance_ratio"),
        ])
        signal_set = SignalEngine(SignalEngineConfig(decline_rules=(
            DeclineSignalRule("participation_decline", "participation", "participation_score", 1, 4, None, "p.v1"),
            DeclineSignalRule("attendance_decline", "attendance", "attendance_ratio", 0.2, 4, None, "a.v1"),
        ))).generate(analyses)
        alerts = AlertEngine(AlertEngineConfig(
            rules=(
                AlertRule(("participation_decline", "attendance_decline"), AlertLevel.CRITICO, "alert.critical.v1"),
                AlertRule(("participation_decline",), AlertLevel.OBSERVAR, "alert.observe.v1"),
            ),
            required_evaluations=(("participation", "participation_score"), ("attendance", "attendance_ratio")),
        )).evaluate(signal_set)
        self.assertEqual(alerts[0].level, AlertLevel.CRITICO)
        self.assertEqual({item.signal_type for item in alerts[0].signals}, {"participation_decline", "attendance_decline"})

    def test_insufficient_required_dimension_is_not_normal(self):
        analyses = analysis([obs("s1", 1), obs("s2", 2)], minimum=4)
        signal_set = SignalEngine(SignalEngineConfig()).generate(analyses)
        alerts = AlertEngine(AlertEngineConfig(required_evaluations=(("participation", "participation_score"),))).evaluate(signal_set)
        self.assertEqual(alerts[0].evaluation_status, AlertEvaluationStatus.INSUFFICIENT_DATA)
        self.assertIsNone(alerts[0].level)

    def test_sufficient_no_signal_is_normal(self):
        analyses = analysis([obs("s1", 1), obs("s2", 1), obs("s3", 1), obs("s4", 1)])
        signal_set = SignalEngine(SignalEngineConfig()).generate(analyses)
        alerts = AlertEngine(AlertEngineConfig(required_evaluations=(("participation", "participation_score"),))).evaluate(signal_set)
        self.assertEqual((alerts[0].evaluation_status, alerts[0].level), (AlertEvaluationStatus.EVALUATED, AlertLevel.NORMAL))

    def test_recovery_does_not_create_risk_without_alert_rule(self):
        analyses = analysis([obs("s1", 3), obs("s2", 1), obs("s3", 1), obs("s4", 2), obs("s5", 3), obs("s6", 3)], window=2, minimum=6)
        signal_set = SignalEngine(SignalEngineConfig(
            recovery_rules=(RecoverySignalRule("recovery", "participation", "participation_score", 0.5, 1, 6, "r.v1"),),
        )).generate(analyses)
        alerts = AlertEngine(AlertEngineConfig(required_evaluations=(("participation", "participation_score"),))).evaluate(signal_set)
        self.assertEqual(alerts[0].level, AlertLevel.NORMAL)


if __name__ == "__main__":
    unittest.main()
