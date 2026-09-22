from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class Signal:
    participant_id: str
    signal_type: str
    dimensions: tuple[str, ...]
    session_ids: tuple[str, ...]
    evidence: Mapping[str, object] = field(default_factory=dict)
    rule_version: str = ""


@dataclass(frozen=True)
class EvaluationContext:
    participant_id: str
    dimension: str
    metric: str
    sufficient_data: bool
    current_evaluable: bool = True


@dataclass(frozen=True)
class SignalSet:
    signals: tuple[Signal, ...]
    evaluations: tuple[EvaluationContext, ...]
