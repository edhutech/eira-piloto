from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .signals import Signal


class AlertLevel(str, Enum):
    NORMAL = "NORMAL"
    OBSERVAR = "OBSERVAR"
    CRITICO = "CRÍTICO"


class AlertEvaluationStatus(str, Enum):
    EVALUATED = "EVALUATED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class Alert:
    participant_id: str
    evaluation_status: AlertEvaluationStatus
    level: AlertLevel | None
    signals: tuple[Signal, ...]
    explanation: Mapping[str, object]
    rule_version: str
