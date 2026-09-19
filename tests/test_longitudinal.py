import unittest
from pathlib import Path

from participacion.core.longitudinal import (
    LongitudinalConfig,
    StreakRule,
    analyze_longitudinal,
)
from participacion.core.observation import (
    Observation,
    ObservationProvenance,
    ObservationStatus,
)


SESSION_ORDER = {"s1": 1, "s2": 2, "s3": 3, "s4": 4, "s5": 5}


def observed(session, value, *, participant="p1", metric="metric", dimension="dimension"):
    return Observation(
        participant, session, dimension, metric, value,
        ObservationStatus.OBSERVED,
        (ObservationProvenance("source", f"row:{session}"),),
    )


def unavailable(session, status, *, participant="p1", metric="metric", dimension="dimension"):
    return Observation(participant, session, dimension, metric, None, status)


class LongitudinalTests(unittest.TestCase):
    def test_numeric_baseline_recent_average_and_delta(self):
        result = analyze_longitudinal(
            [observed("s1", 0.2), observed("s2", 0.4), observed("s3", 0.6), observed("s4", 0.8)],
            SESSION_ORDER,
            LongitudinalConfig(minimum_observations=4, recent_window_size=2),
        )[0]
        self.assertEqual(result.observed_count, 4)
        self.assertEqual(result.applicable_count, 4)
        self.assertEqual(result.recent_window_size, 2)
        self.assertAlmostEqual(result.baseline, 0.3)
        self.assertEqual(result.recent_value, 0.8)
        self.assertAlmostEqual(result.recent_average, 0.7)
        self.assertAlmostEqual(result.delta, 0.4)
        self.assertTrue(result.sufficient_data)

    def test_recent_window_is_configurable_and_can_consume_all_data(self):
        observations = [observed("s1", 1), observed("s2", 2), observed("s3", 3)]
        result = analyze_longitudinal(
            observations, SESSION_ORDER,
            LongitudinalConfig(minimum_observations=2, recent_window_size=3),
        )[0]
        self.assertEqual(result.recent_window_size, 3)
        self.assertIsNone(result.baseline)
        self.assertEqual(result.recent_average, 2.0)
        self.assertIsNone(result.delta)

    def test_insufficient_data_is_not_a_risk_classification(self):
        result = analyze_longitudinal(
            [observed("s1", 1), observed("s2", 2)],
            SESSION_ORDER,
            LongitudinalConfig(minimum_observations=4, recent_window_size=1),
        )[0]
        self.assertEqual(result.observed_count, 2)
        self.assertFalse(result.sufficient_data)
        self.assertEqual(result.trend, "insufficient_data")

    def test_no_data_and_incomplete_are_not_zero(self):
        result = analyze_longitudinal(
            [observed("s1", 1), unavailable("s2", ObservationStatus.NO_DATA),
             unavailable("s3", ObservationStatus.INCOMPLETE), observed("s4", 3)],
            SESSION_ORDER,
            LongitudinalConfig(minimum_observations=2, recent_window_size=2),
        )[0]
        self.assertEqual(result.applicable_count, 4)
        self.assertEqual(result.observed_count, 2)
        self.assertEqual(result.recent_window_size, 2)
        self.assertEqual(result.recent_observed_count, 1)
        self.assertEqual(result.recent_average, 3.0)
        self.assertEqual(result.baseline, 1.0)
        self.assertEqual(result.delta, 2.0)

    def test_not_applicable_is_excluded_from_applicable_counts_and_windows(self):
        result = analyze_longitudinal(
            [observed("s1", 1), unavailable("s2", ObservationStatus.NOT_APPLICABLE), observed("s3", 3)],
            SESSION_ORDER,
            LongitudinalConfig(minimum_observations=2, recent_window_size=1),
        )[0]
        self.assertEqual(result.applicable_count, 2)
        self.assertEqual(result.recent_window_size, 1)
        self.assertEqual(result.recent_average, 3.0)
        self.assertEqual(result.baseline, 1.0)

    def test_explicit_session_order_not_identifier_order(self):
        observations = [observed("s1", 10), observed("s2", 20)]
        result = analyze_longitudinal(
            observations, {"s1": 20, "s2": 10},
            LongitudinalConfig(minimum_observations=2, recent_window_size=1),
        )[0]
        self.assertEqual(result.recent_value, 10)
        self.assertEqual(result.baseline, 20.0)

    def test_results_are_deterministic_independent_of_input_order(self):
        observations = [observed("s3", 3), observed("s1", 1), observed("s2", 2)]
        config = LongitudinalConfig(minimum_observations=2, recent_window_size=2)
        first = analyze_longitudinal(observations, SESSION_ORDER, config)
        second = analyze_longitudinal(list(reversed(observations)), SESSION_ORDER, config)
        self.assertEqual(first, second)

    def test_duplicate_observation_is_rejected(self):
        observations = [observed("s1", 1), observed("s1", 2)]
        with self.assertRaisesRegex(ValueError, "duplicada"):
            analyze_longitudinal(observations, SESSION_ORDER,
                                 LongitudinalConfig(minimum_observations=1, recent_window_size=1))

    def test_streaks_use_explicit_generic_predicate(self):
        rule = StreakRule("above_threshold", lambda item: None if item.status is not ObservationStatus.OBSERVED else item.value >= 2)
        result = analyze_longitudinal(
            [observed("s1", 1), observed("s2", 2), observed("s3", 3), unavailable("s4", ObservationStatus.NO_DATA), observed("s5", 4)],
            SESSION_ORDER,
            LongitudinalConfig(minimum_observations=3, recent_window_size=2, streak_rules=(rule,)),
        )[0]
        streak = result.streaks[0]
        self.assertEqual((streak.current_length, streak.maximum_length, streak.evaluated_count), (1, 2, 4))

    def test_trend_is_descriptive_and_deterministic(self):
        result = analyze_longitudinal(
            [observed("s1", 1), observed("s2", 2), observed("s3", 3)],
            SESSION_ORDER,
            LongitudinalConfig(minimum_observations=2, recent_window_size=1, trend_threshold=0.1),
        )[0]
        self.assertEqual(result.trend, "increasing")

    def test_metrics_and_participants_are_not_mixed(self):
        observations = [
            observed("s1", 1, metric="m1"), observed("s2", 2, metric="m1"),
            observed("s1", 100, metric="m2"), observed("s2", 200, metric="m2"),
            observed("s1", 50, participant="p2"), observed("s2", 60, participant="p2"),
        ]
        results = analyze_longitudinal(
            observations, SESSION_ORDER,
            LongitudinalConfig(minimum_observations=2, recent_window_size=1),
        )
        self.assertEqual(len(results), 3)
        self.assertEqual({(item.participant_id, item.metric) for item in results},
                         {("p1", "m1"), ("p1", "m2"), ("p2", "metric")})

    def test_provenance_supports_multiple_evidences(self):
        observation = Observation(
            "p1", "s1", "d", "m", 1, ObservationStatus.OBSERVED,
            (ObservationProvenance("a", "row:1"), ObservationProvenance("b", "event:2")),
        )
        self.assertEqual(len(observation.provenance), 2)

    def test_core_does_not_import_ingestion_or_provider_concepts(self):
        for path in Path("src/participacion/core").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for forbidden in ("external_data", "adapters", "openpyxl", "import csv", "CanonicalFact", "BASF", "Google"):
                self.assertNotIn(forbidden, text, path)


if __name__ == "__main__":
    unittest.main()
