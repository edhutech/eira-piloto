from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from ...core.alerts import Alert
from ...core.observation import ObservationStatus
from ...core.signals import Signal
from .results import HistoricalSnapshot, PilotResult


@dataclass(frozen=True)
class OperationalSignal:
    signal_type: str
    dimensions: tuple[str, ...]
    session_ids: tuple[str, ...]
    evidence: Mapping[str, object]
    rule_version: str

    @classmethod
    def from_signal(cls, signal: Signal) -> "OperationalSignal":
        return cls(signal.signal_type, signal.dimensions, signal.session_ids,
                   dict(signal.evidence), signal.rule_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "dimensions": list(self.dimensions),
            "session_ids": list(self.session_ids),
            "evidence": dict(self.evidence),
            "rule_version": self.rule_version,
        }


@dataclass(frozen=True)
class OperationalCase:
    participant_id: str
    as_of_session_id: str
    as_of_order: int
    evaluation_status: str
    alert_level: str | None
    signals: tuple[OperationalSignal, ...]
    explanation: Mapping[str, object]
    rule_version: str

    @classmethod
    def from_alert(cls, alert: Alert, snapshot: HistoricalSnapshot) -> "OperationalCase":
        return cls(
            participant_id=alert.participant_id,
            as_of_session_id=snapshot.as_of_session_id,
            as_of_order=snapshot.as_of_order,
            evaluation_status=alert.evaluation_status.value,
            alert_level=alert.level.value if alert.level is not None else None,
            signals=tuple(OperationalSignal.from_signal(signal) for signal in alert.signals),
            explanation=dict(alert.explanation),
            rule_version=alert.rule_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "as_of_session_id": self.as_of_session_id,
            "as_of_order": self.as_of_order,
            "evaluation_status": self.evaluation_status,
            "alert_level": self.alert_level,
            "signals": [signal.to_dict() for signal in self.signals],
            "explanation": dict(self.explanation),
            "rule_version": self.rule_version,
        }


@dataclass(frozen=True)
class OperationalView:
    as_of_session_id: str
    as_of_order: int
    cases: tuple[OperationalCase, ...]
    signal_counts: Mapping[str, int]
    attendance_observed_count: int
    generated_at: str

    def summary(self) -> dict[str, int]:
        result = {"NORMAL": 0, "OBSERVAR": 0, "INSUFFICIENT_DATA": 0}
        for case in self.cases:
            key = case.alert_level if case.evaluation_status == "EVALUATED" else "INSUFFICIENT_DATA"
            if key in result:
                result[key] += 1
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "as_of_session_id": self.as_of_session_id,
            "as_of_order": self.as_of_order,
            "signal_counts": dict(self.signal_counts),
            "attendance_observed_count": self.attendance_observed_count,
            "generated_at": self.generated_at,
            "cases": [case.to_dict() for case in self.cases],
        }


def build_operational_view(result: PilotResult) -> OperationalView:
    if not result.snapshots:
        return OperationalView("", 0, (), {}, 0, _now())
    snapshot = result.snapshots[-1]
    cases = tuple(OperationalCase.from_alert(alert, snapshot) for alert in snapshot.alerts)
    signal_counts: dict[str, int] = {}
    for case in cases:
        for signal in case.signals:
            signal_counts[signal.signal_type] = signal_counts.get(signal.signal_type, 0) + 1
    attendance_observed_count = sum(
        1 for observation in snapshot.observations
        if observation.dimension == "attendance" and observation.status is ObservationStatus.OBSERVED
    )
    return OperationalView(snapshot.as_of_session_id, snapshot.as_of_order, cases,
                           signal_counts, attendance_observed_count, _now())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
