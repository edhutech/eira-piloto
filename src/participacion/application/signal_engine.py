from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..core.longitudinal import LongitudinalAnalysis
from ..core.observation import ObservationStatus
from ..core.signals import EvaluationContext, Signal, SignalSet


@dataclass(frozen=True)
class DeclineSignalRule:
    signal_type: str
    dimension: str
    metric: str
    delta_threshold: float
    minimum_observations: int
    required_trend: str | None
    rule_version: str


@dataclass(frozen=True)
class StreakSignalRule:
    signal_type: str
    dimension: str
    metric: str
    streak_name: str
    minimum_current_streak: int
    rule_version: str


@dataclass(frozen=True)
class RecoverySignalRule:
    signal_type: str
    dimension: str
    metric: str
    deterioration_threshold: float
    improvement_threshold: float
    minimum_observations: int
    rule_version: str


@dataclass(frozen=True)
class SignalEngineConfig:
    decline_rules: tuple[DeclineSignalRule, ...] = ()
    streak_rules: tuple[StreakSignalRule, ...] = ()
    recovery_rules: tuple[RecoverySignalRule, ...] = ()


class SignalEngine:
    def __init__(self, config: SignalEngineConfig):
        self.config = config

    def generate(self, analyses: Iterable[LongitudinalAnalysis]) -> SignalSet:
        analysis_list = tuple(sorted(analyses, key=lambda item: (item.participant_id, item.dimension, item.metric)))
        evaluations = tuple(
            EvaluationContext(
                item.participant_id,
                item.dimension,
                item.metric,
                item.sufficient_data,
                item.current_status is ObservationStatus.OBSERVED,
            )
            for item in analysis_list
        )
        signals: list[Signal] = []
        for analysis in analysis_list:
            signals.extend(self._declines(analysis))
            signals.extend(self._streaks(analysis))
            signals.extend(self._recoveries(analysis))
        return SignalSet(tuple(signals), evaluations)

    def _declines(self, analysis: LongitudinalAnalysis) -> list[Signal]:
        result: list[Signal] = []
        for rule in self.config.decline_rules:
            if not self._matches(analysis, rule.dimension, rule.metric):
                continue
            if (not analysis.sufficient_data or analysis.observed_count < rule.minimum_observations or
                    analysis.delta is None or analysis.delta > -rule.delta_threshold):
                continue
            if rule.required_trend is not None and analysis.trend != rule.required_trend:
                continue
            result.append(Signal(
                analysis.participant_id, rule.signal_type, (analysis.dimension,), analysis.recent_session_ids,
                {"metric": analysis.metric, "baseline": analysis.baseline,
                 "recent_average": analysis.recent_average, "delta": analysis.delta,
                 "trend": analysis.trend, "observed_count": analysis.observed_count,
                 "minimum_observations": rule.minimum_observations}, rule.rule_version,
            ))
        return result

    def _streaks(self, analysis: LongitudinalAnalysis) -> list[Signal]:
        result: list[Signal] = []
        for rule in self.config.streak_rules:
            if not self._matches(analysis, rule.dimension, rule.metric) or not analysis.sufficient_data:
                continue
            streak = next((item for item in analysis.streaks if item.name == rule.streak_name), None)
            if streak is None or streak.current_length < rule.minimum_current_streak:
                continue
            result.append(Signal(
                analysis.participant_id, rule.signal_type, (analysis.dimension,), analysis.recent_session_ids,
                {"metric": analysis.metric, "streak_name": streak.name,
                 "current_streak": streak.current_length,
                 "minimum_current_streak": rule.minimum_current_streak}, rule.rule_version,
            ))
        return result

    def _recoveries(self, analysis: LongitudinalAnalysis) -> list[Signal]:
        result: list[Signal] = []
        for rule in self.config.recovery_rules:
            if not self._matches(analysis, rule.dimension, rule.metric) or not analysis.sufficient_data:
                continue
            if (analysis.baseline is None or analysis.previous_recent_average is None or
                    analysis.recent_average is None or not analysis.previous_recent_session_ids):
                continue
            deteriorated = analysis.previous_recent_average <= analysis.baseline - rule.deterioration_threshold
            improved = analysis.recent_average >= analysis.previous_recent_average + rule.improvement_threshold
            if not (deteriorated and improved):
                continue
            result.append(Signal(
                analysis.participant_id, rule.signal_type, (analysis.dimension,),
                analysis.previous_recent_session_ids + analysis.recent_session_ids,
                {"metric": analysis.metric, "baseline": analysis.baseline,
                 "previous_recent_average": analysis.previous_recent_average,
                 "recent_average": analysis.recent_average,
                 "improvement": analysis.recent_average - analysis.previous_recent_average,
                 "deterioration_threshold": rule.deterioration_threshold,
                 "improvement_threshold": rule.improvement_threshold}, rule.rule_version,
            ))
        return result

    @staticmethod
    def _matches(analysis: LongitudinalAnalysis, dimension: str, metric: str) -> bool:
        return analysis.dimension == dimension and analysis.metric == metric
