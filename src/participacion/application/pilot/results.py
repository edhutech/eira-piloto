from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from ...core.alerts import Alert
from ...core.longitudinal import LongitudinalAnalysis
from ...core.observation import Observation
from ...core.signals import SignalSet


@dataclass(frozen=True)
class HistoricalSnapshot:
    as_of_session_id: str
    as_of_order: int
    observations: tuple[Observation, ...]
    analyses: tuple[LongitudinalAnalysis, ...]
    signal_set: SignalSet
    alerts: tuple[Alert, ...]
    issues: tuple[object, ...] = ()
    operational: bool = True


@dataclass(frozen=True)
class PilotResult:
    program_id: str
    snapshots: tuple[HistoricalSnapshot, ...]
    observations: tuple[Observation, ...]
    issues: tuple[object, ...]

    @property
    def snapshot_count(self) -> int:
        return len(self.snapshots)

    @property
    def first_session_id(self) -> str | None:
        return self.snapshots[0].as_of_session_id if self.snapshots else None

    @property
    def last_session_id(self) -> str | None:
        return self.snapshots[-1].as_of_session_id if self.snapshots else None

    def issue_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in self.issues:
            key = str(getattr(issue, "code", type(issue).__name__))
            counts[key] = counts.get(key, 0) + 1
        return counts


@dataclass(frozen=True)
class OutcomeRecord:
    participant_id: str
    occurred_at: datetime
    outcome_type: str
    provenance: str


@dataclass(frozen=True)
class RetrospectiveReport:
    outcomes_evaluable: int
    cases_with_prior_signal: int
    cases_with_prior_alert: int
    cases_with_prior_observar: int
    cases_with_prior_critical: int
    cases_without_prior_signal: int
    alerts_without_outcome: int
    insufficient_data_snapshots: int
    first_signal_by_participant: Mapping[str, int]
    first_alert_by_participant: Mapping[str, int]


class RetrospectiveEvaluator:
    """Compare temporal snapshots with outcomes, without changing pilot policy."""

    def evaluate(
        self,
        result: PilotResult,
        outcomes: Iterable[OutcomeRecord],
        session_dates: Mapping[str, datetime],
    ) -> RetrospectiveReport:
        ordered = tuple(sorted(result.snapshots, key=lambda item: item.as_of_order))
        outcomes_tuple = tuple(outcomes)
        first_signal: dict[str, int] = {}
        first_alert: dict[str, int] = {}
        alert_participants: set[str] = set()
        for snapshot in ordered:
            for signal in snapshot.signal_set.signals:
                first_signal.setdefault(signal.participant_id, snapshot.as_of_order)
            for alert in snapshot.alerts:
                if getattr(alert.level, "value", None) in {"OBSERVAR", "CRÍTICO"}:
                    first_alert.setdefault(alert.participant_id, snapshot.as_of_order)
                    alert_participants.add(alert.participant_id)
        evaluable = 0
        with_signal = 0
        with_alert = 0
        with_observar = 0
        with_critical = 0
        without_signal = 0
        matched_outcome_participants: set[str] = set()
        for outcome in outcomes_tuple:
            prior = tuple(
                snapshot for snapshot in ordered
                if session_dates.get(snapshot.as_of_session_id) is not None
                and session_dates[snapshot.as_of_session_id] < outcome.occurred_at
            )
            if not prior:
                continue
            evaluable += 1
            participant_signals = [
                signal for snapshot in prior for signal in snapshot.signal_set.signals
                if signal.participant_id == outcome.participant_id
            ]
            participant_alerts = [
                alert for snapshot in prior for alert in snapshot.alerts
                if alert.participant_id == outcome.participant_id
            ]
            matched_outcome_participants.add(outcome.participant_id)
            if participant_signals:
                with_signal += 1
            else:
                without_signal += 1
            levels = {getattr(alert.level, "value", None) for alert in participant_alerts}
            if participant_alerts:
                with_alert += 1
            if "OBSERVAR" in levels:
                with_observar += 1
            if "CRÍTICO" in levels:
                with_critical += 1
        return RetrospectiveReport(
            outcomes_evaluable=evaluable,
            cases_with_prior_signal=with_signal,
            cases_with_prior_alert=with_alert,
            cases_with_prior_observar=with_observar,
            cases_with_prior_critical=with_critical,
            cases_without_prior_signal=without_signal,
            alerts_without_outcome=len(alert_participants - matched_outcome_participants),
            insufficient_data_snapshots=sum(
                1 for snapshot in ordered
                for alert in snapshot.alerts
                if alert.evaluation_status.value == "INSUFFICIENT_DATA"
            ),
            first_signal_by_participant=first_signal,
            first_alert_by_participant=first_alert,
        )
