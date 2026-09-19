from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterable, Mapping, Sequence

from .observation import Observation, ObservationStatus


Predicate = Callable[[Observation], bool | None]


@dataclass(frozen=True)
class StreakRule:
    name: str
    predicate: Predicate

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Una regla de streak requiere nombre")


@dataclass(frozen=True)
class LongitudinalConfig:
    minimum_observations: int = 1
    recent_window_size: int = 1
    trend_threshold: float = 0.0
    streak_rules: tuple[StreakRule, ...] = ()

    def __post_init__(self) -> None:
        if self.minimum_observations < 1:
            raise ValueError("minimum_observations debe ser positivo")
        if self.recent_window_size < 1:
            raise ValueError("recent_window_size debe ser positivo")
        if self.trend_threshold < 0:
            raise ValueError("trend_threshold no puede ser negativo")


@dataclass(frozen=True)
class StreakAnalysis:
    name: str
    current_length: int
    maximum_length: int
    evaluated_count: int


@dataclass(frozen=True)
class LongitudinalAnalysis:
    participant_id: str
    dimension: str
    metric: str
    observed_count: int
    applicable_count: int
    recent_window_size: int
    recent_observed_count: int
    sufficient_data: bool
    baseline: float | None
    baseline_observed_count: int
    recent_value: object | None
    recent_average: float | None
    delta: float | None
    streaks: tuple[StreakAnalysis, ...]
    trend: str | None
    baseline_session_ids: tuple[str, ...] = ()
    recent_session_ids: tuple[str, ...] = ()
    previous_recent_session_ids: tuple[str, ...] = ()
    previous_recent_average: float | None = None


def analyze_longitudinal(
    observations: Iterable[Observation],
    session_order: Mapping[str, int],
    config: LongitudinalConfig,
) -> tuple[LongitudinalAnalysis, ...]:
    _validate_session_order(session_order)
    groups: dict[tuple[str, str, str], list[Observation]] = {}
    seen: set[tuple[str, str, str, str]] = set()
    for observation in observations:
        if observation.session_id not in session_order:
            raise ValueError(f"Sesión sin orden explícito: {observation.session_id}")
        key = (observation.participant_id, observation.dimension, observation.metric, observation.session_id)
        if key in seen:
            raise ValueError(f"Observation duplicada: {key}")
        seen.add(key)
        group_key = key[:3]
        groups.setdefault(group_key, []).append(observation)

    results = [
        _analyze_group(group_key, values, session_order, config)
        for group_key, values in sorted(groups.items())
    ]
    return tuple(results)


def _analyze_group(
    group_key: tuple[str, str, str],
    observations: Sequence[Observation],
    session_order: Mapping[str, int],
    config: LongitudinalConfig,
) -> LongitudinalAnalysis:
    ordered = sorted(observations, key=lambda item: session_order[item.session_id])
    applicable = [item for item in ordered if item.status is not ObservationStatus.NOT_APPLICABLE]
    recent = applicable[-config.recent_window_size:]
    previous = applicable[:-len(recent)] if recent else []
    previous_recent = previous[-config.recent_window_size:]
    observed = [item for item in applicable if item.status is ObservationStatus.OBSERVED]
    recent_observed = [item for item in recent if item.status is ObservationStatus.OBSERVED]
    baseline_values = _numeric_values(previous)
    recent_values = _numeric_values(recent_observed)
    previous_recent_values = _numeric_values(previous_recent)
    baseline = _average(baseline_values)
    recent_average = _average(recent_values)
    previous_recent_average = _average(previous_recent_values)
    delta = None if baseline is None or recent_average is None else recent_average - baseline
    sufficient = len(observed) >= config.minimum_observations
    numeric_series = [(_number(item.value), index) for index, item in enumerate(ordered)
                      if item.status is ObservationStatus.OBSERVED and _number(item.value) is not None]
    trend = _trend(numeric_series, config.trend_threshold) if sufficient else "insufficient_data"
    streaks = tuple(_streak(rule, applicable) for rule in config.streak_rules)
    return LongitudinalAnalysis(
        participant_id=group_key[0],
        dimension=group_key[1],
        metric=group_key[2],
        observed_count=len(observed),
        applicable_count=len(applicable),
        recent_window_size=len(recent),
        recent_observed_count=len(recent_observed),
        sufficient_data=sufficient,
        baseline=baseline,
        baseline_observed_count=len(baseline_values),
        recent_value=recent_observed[-1].value if recent_observed else None,
        recent_average=recent_average,
        delta=delta,
        streaks=streaks,
        trend=trend,
        baseline_session_ids=tuple(item.session_id for item in previous),
        recent_session_ids=tuple(item.session_id for item in recent),
        previous_recent_session_ids=tuple(item.session_id for item in previous_recent),
        previous_recent_average=previous_recent_average,
    )


def _validate_session_order(session_order: Mapping[str, int]) -> None:
    if len(set(session_order.values())) != len(session_order):
        raise ValueError("session_order contiene posiciones duplicadas")
    for session_id, order in session_order.items():
        if not str(session_id).strip() or isinstance(order, bool) or not isinstance(order, int):
            raise ValueError("session_order requiere IDs y posiciones enteras válidas")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _numeric_values(observations: Sequence[Observation]) -> list[float]:
    values: list[float] = []
    for observation in observations:
        if observation.status is not ObservationStatus.OBSERVED:
            continue
        number = _number(observation.value)
        if number is not None:
            values.append(number)
    return values


def _average(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _trend(series: Sequence[tuple[float | None, int]], threshold: float) -> str:
    points = [(float(index), value) for value, index in series if value is not None]
    if len(points) < 2:
        return "insufficient_data"
    mean_x = sum(item[0] for item in points) / len(points)
    mean_y = sum(item[1] for item in points) / len(points)
    denominator = sum((item[0] - mean_x) ** 2 for item in points)
    slope = 0.0 if denominator == 0 else sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
    if slope > threshold:
        return "increasing"
    if slope < -threshold:
        return "decreasing"
    return "stable"


def _streak(rule: StreakRule, observations: Sequence[Observation]) -> StreakAnalysis:
    current = 0
    maximum = 0
    evaluated = 0
    for observation in observations:
        result = rule.predicate(observation)
        if result is None:
            current = 0
            continue
        evaluated += 1
        if result:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return StreakAnalysis(rule.name, current, maximum, evaluated)
