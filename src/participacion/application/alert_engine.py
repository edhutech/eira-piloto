from __future__ import annotations

from dataclasses import dataclass
from ..core.alerts import Alert, AlertEvaluationStatus, AlertLevel
from ..core.signals import EvaluationContext, Signal, SignalSet


@dataclass(frozen=True)
class AlertRule:
    required_signal_types: tuple[str, ...]
    level: AlertLevel
    rule_version: str

    def __post_init__(self) -> None:
        if not self.required_signal_types:
            raise ValueError("AlertRule requiere al menos un signal")


@dataclass(frozen=True)
class AlertEngineConfig:
    rules: tuple[AlertRule, ...] = ()
    required_evaluations: tuple[tuple[str, str], ...] = ()
    default_rule_version: str = "alert.default.v1"


class AlertEngine:
    def __init__(self, config: AlertEngineConfig):
        self.config = config

    def evaluate(self, signal_set: SignalSet) -> tuple[Alert, ...]:
        participant_ids = {item.participant_id for item in signal_set.evaluations}
        participant_ids.update(item.participant_id for item in signal_set.signals)
        result: list[Alert] = []
        for participant_id in sorted(participant_ids):
            signals = tuple(item for item in signal_set.signals if item.participant_id == participant_id)
            contexts = tuple(item for item in signal_set.evaluations if item.participant_id == participant_id)
            matched = self._matched_rules(signals)
            if matched:
                rule, contributing = matched[0]
                result.append(Alert(
                    participant_id, AlertEvaluationStatus.EVALUATED, rule.level, contributing,
                    {"matched_signal_types": tuple(item.signal_type for item in contributing),
                     "rule": rule.required_signal_types}, rule.rule_version,
                ))
                continue
            sufficient = self._sufficient(contexts)
            if sufficient:
                result.append(Alert(
                    participant_id, AlertEvaluationStatus.EVALUATED, AlertLevel.NORMAL, signals,
                    {"reason": "no_alert_rule_activated"}, self.config.default_rule_version,
                ))
            else:
                result.append(Alert(
                    participant_id, AlertEvaluationStatus.INSUFFICIENT_DATA, None, signals,
                    {"reason": "required_evaluation_insufficient"}, self.config.default_rule_version,
                ))
        return tuple(result)

    def _matched_rules(self, signals: tuple[Signal, ...]) -> list[tuple[AlertRule, tuple[Signal, ...]]]:
        available = {item.signal_type for item in signals}
        matches: list[tuple[AlertRule, tuple[Signal, ...]]] = []
        for rule in self.config.rules:
            if set(rule.required_signal_types).issubset(available):
                selected = tuple(item for item in signals if item.signal_type in rule.required_signal_types)
                matches.append((rule, selected))
        priority = {AlertLevel.CRITICO: 2, AlertLevel.OBSERVAR: 1, AlertLevel.NORMAL: 0}
        return sorted(matches, key=lambda item: (-priority[item[0].level], item[0].rule_version))

    def _sufficient(self, contexts: tuple[EvaluationContext, ...]) -> bool:
        by_key = {
            (item.dimension, item.metric): item.sufficient_data and item.current_evaluable
            for item in contexts
        }
        required = self.config.required_evaluations or tuple(by_key)
        return bool(required) and all(by_key.get(key, False) for key in required)
