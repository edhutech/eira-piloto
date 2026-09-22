from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from ...application.external_data.mapping import MappingStatus, map_table
from ...application.external_data.models import TabularTable
from ...application.modules.attendance import AttendanceConfig, AttendanceInput, AttendanceModule
from ...application.modules.models import (ApplicabilityResolver, ApplicabilityResult, ApplicabilityStatus,
                                            ParticipantSessionPair)
from ...application.modules.participation import ParticipationConfig, ParticipationModule
from ...application.modules.resolution import ExternalParticipantResolver, MappingSessionResolver
from ...core.alerts import AlertLevel
from ...core.longitudinal import StreakRule, analyze_longitudinal

from ...core.observation import Observation, ObservationStatus
from ...core.participants import Participant
from ...core.scoring import ParticipantSessionScore
from ..alert_engine import AlertEngine, AlertEngineConfig, AlertRule
from ..signal_engine import SignalEngine, SignalEngineConfig, StreakSignalRule
from .config import PilotConfig
from .results import HistoricalSnapshot, PilotResult


@dataclass(frozen=True)
class PilotSources:
    participant_scores: Callable[[], Iterable[ParticipantSessionScore]]
    attendance_table: Callable[[], TabularTable] | None
    participant_resolver: ExternalParticipantResolver
    applicability_resolver: ApplicabilityResolver | None = None
    participant_ids: Callable[[], Iterable[str]] | None = None
    participation_statuses: Callable[[], Mapping[str, str]] | None = None


class PilotRunner:
    """Compose the read-only pilot pipeline without owning providers or writes."""

    def __init__(self, config: PilotConfig, sources: PilotSources):
        self.config = config
        self.sources = sources
        self._longitudinal_config = config.longitudinal

    def run(self) -> PilotResult:
        session_order = {item.session_id: item.session_order for item in self.config.session_mapping.values()}
        session_by_external = {key: item.session_id for key, item in self.config.session_mapping.items()}
        applicability = self.sources.applicability_resolver or AlwaysApplicable()
        participation_module = ParticipationModule(
            MappingSessionResolver({str(order): session_id for session_id, order in session_order.items()}),
            applicability,
            ParticipationConfig(f"google_sheets:{self.config.google_sheet_id}", coverage=self.config.participation_coverage),
        )
        participant_ids = tuple(self.sources.participant_ids() if self.sources.participant_ids else ())
        expected_pairs = tuple(
            ParticipantSessionPair(participant_id, str(order))
            for participant_id in participant_ids
            for _, order in session_order.items()
        )
        participation_result = participation_module.build_observations(
            self.sources.participant_scores(),
            expected_pairs=expected_pairs,
            session_statuses=(self.sources.participation_statuses() if self.sources.participation_statuses else {}),
        )

        attendance_observations: list[Observation] = []
        attendance_issues: tuple[object, ...] = ()
        if self.config.attendance_mapping is not None:
            if self.sources.attendance_table is None:
                raise ValueError("Attendance está configurado pero no existe attendance_table")
            mapping_result = map_table(self.sources.attendance_table(), self.config.attendance_mapping)
            attendance_resolver = MappingSessionResolver(session_by_external)
            attendance_module = AttendanceModule(
                self.sources.participant_resolver,
                attendance_resolver,
                applicability,
                AttendanceConfig({
                    metric: metric for metric in self.config.attendance_mapping.metrics
                }),
            )
            attendance_result = attendance_module.build_observations(
                AttendanceInput(mapping_result.facts, coverage=self.config.attendance_coverage)
            )
            attendance_observations = list(attendance_result.observations)
            attendance_issues = tuple(attendance_result.issues)
            if mapping_result.status is not MappingStatus.VALID:
                attendance_issues += tuple(mapping_result.issues)

        observations = tuple(list(participation_result.observations) + attendance_observations)
        issues: tuple[object, ...] = tuple(participation_result.issues) + attendance_issues

        signal_engine = self._signal_engine()
        alert_engine = self._alert_engine()
        snapshots: list[HistoricalSnapshot] = []
        ordered_sessions = sorted(session_order.items(), key=lambda item: item[1])
        statuses = self.sources.participation_statuses() if self.sources.participation_statuses else {}
        for session_id, order in ordered_sessions:
            available = tuple(item for item in observations if session_order[item.session_id] <= order)
            analyses = analyze_longitudinal(available, session_order, self._longitudinal_config)
            signal_set = signal_engine.generate(analyses)
            alerts = alert_engine.evaluate(signal_set)
            snapshots.append(HistoricalSnapshot(
                as_of_session_id=session_id,
                as_of_order=order,
                observations=available,
                analyses=analyses,
                signal_set=signal_set,
                alerts=alerts,
                issues=issues,
                operational=(statuses.get(str(order)) in {
                    "PROCESSED", "INCOMPLETE", "NEEDS_REVIEW", "FAILED"
                } if statuses else bool(available)),
            ))
        return PilotResult(self.config.program_id, tuple(snapshots), observations, issues)

    def _signal_engine(self) -> SignalEngine:
        streak_rules: tuple[StreakSignalRule, ...] = ()
        longitudinal_streak_rules: tuple[StreakRule, ...] = ()
        if self.config.silence_enabled:
            longitudinal_streak_rules = (StreakRule("participation_zero", zero_observed_streak_rule),)
            streak_rules = (StreakSignalRule(
                "participation_silence_streak", "participation", "participation_score",
                "participation_zero", self.config.silence_minimum_streak, self.config.signal_version,
            ),)
        self._longitudinal_config = type(self.config.longitudinal)(
            minimum_observations=self.config.longitudinal.minimum_observations,
            recent_window_size=self.config.longitudinal.recent_window_size,
            trend_threshold=self.config.longitudinal.trend_threshold,
            streak_rules=longitudinal_streak_rules,
        )
        return SignalEngine(SignalEngineConfig(streak_rules=streak_rules))

    def _alert_engine(self) -> AlertEngine:
        return AlertEngine(AlertEngineConfig(
            rules=(AlertRule(("participation_silence_streak",), AlertLevel.OBSERVAR, self.config.alert_version),),
            required_evaluations=(("participation", "participation_score"),),
            default_rule_version=self.config.alert_version,
        ))


class AlwaysApplicable:
    def is_applicable(self, participant_id: str, session_id: str) -> ApplicabilityResult:
        return ApplicabilityResult(ApplicabilityStatus.APPLICABLE)


class RosterApplicabilityResolver:
    """Use only explicit roster session bounds; never infer eligibility from outcomes."""

    def __init__(self, participants: Iterable[Participant], session_orders: dict[str, int] | None = None):
        self._participants = {item.participant_id: item for item in participants}
        self._session_orders = session_orders or {}

    def is_applicable(self, participant_id: str, session_id: str) -> ApplicabilityResult:
        participant = self._participants.get(participant_id)
        if participant is None or not participant.enrollment_config_valid:
            return ApplicabilityResult(ApplicabilityStatus.UNKNOWN)
        order = self._session_orders.get(session_id)
        if order is None:
            return ApplicabilityResult(ApplicabilityStatus.UNKNOWN)
        if order < participant.start_session or (participant.end_session is not None and order > participant.end_session):
            return ApplicabilityResult(ApplicabilityStatus.NOT_APPLICABLE)
        return ApplicabilityResult(ApplicabilityStatus.APPLICABLE)


def zero_observed_streak_rule(observation: Observation) -> bool | None:
    if observation.status is ObservationStatus.OBSERVED:
        return observation.value == 0
    return None
