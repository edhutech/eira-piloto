from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ...application.external_data.models import AvailabilityStatus, CanonicalFact
from ...core.observation import Observation, ObservationProvenance, ObservationStatus
from .models import (
    ApplicabilityResolver,
    ApplicabilityStatus,
    ExternalPair,
    ModuleIssue,
    ModuleIssueStatus,
    ModuleResult,
    SourceCoverage,
)
from .resolution import ExternalParticipantResolver, ExternalSessionResolver, ResolutionStatus


@dataclass(frozen=True)
class AttendanceConfig:
    metric_sources: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.metric_sources:
            raise ValueError("AttendanceConfig requiere al menos una métrica explícita")
        if any(not key.strip() or not value.strip() for key, value in self.metric_sources.items()):
            raise ValueError("Las métricas de AttendanceConfig no pueden estar vacías")


@dataclass(frozen=True)
class AttendanceInput:
    facts: tuple[CanonicalFact, ...]
    expected_pairs: tuple[ExternalPair, ...] = ()
    coverage: SourceCoverage = SourceCoverage.UNKNOWN


class AttendanceModule:
    def __init__(self, participant_resolver: ExternalParticipantResolver,
                 session_resolver: ExternalSessionResolver,
                 applicability_resolver: ApplicabilityResolver,
                 config: AttendanceConfig):
        self.participant_resolver = participant_resolver
        self.session_resolver = session_resolver
        self.applicability_resolver = applicability_resolver
        self.config = config

    def build_observations(self, data: AttendanceInput) -> ModuleResult:
        issues: list[ModuleIssue] = []
        by_key: dict[tuple[str, str, str], CanonicalFact] = {}
        duplicate_keys: set[tuple[str, str, str]] = set()
        pair_has_row: set[tuple[str, str]] = set()
        for item in data.facts:
            key = (str(item.participant_external_id).strip(), str(item.session_external_id).strip(), item.metric)
            if not key[0] or not key[1]:
                issues.append(ModuleIssue(ModuleIssueStatus.INVALID, "MISSING_EXTERNAL_ID",
                                          "El hecho no tiene identidad externa completa"))
                continue
            pair_has_row.add(key[:2])
            if key in by_key:
                duplicate_keys.add(key)
                issues.append(ModuleIssue(ModuleIssueStatus.INVALID, "DUPLICATE_FACT",
                                          "Existe más de un CanonicalFact para la misma métrica"))
            else:
                by_key[key] = item
        expected = {(item.participant_external_id, item.session_external_id) for item in data.expected_pairs}
        pairs = sorted(pair_has_row | expected)
        observations: list[Observation] = []
        for participant_external_id, session_external_id in pairs:
            participant = self.participant_resolver.resolve(participant_external_id)
            if participant.status is ResolutionStatus.IGNORED:
                continue
            session = self.session_resolver.resolve(session_external_id)
            if participant.status is not ResolutionStatus.RESOLVED or not participant.internal_id:
                issues.append(ModuleIssue(ModuleIssueStatus.NEEDS_REVIEW, "PARTICIPANT_NOT_RESOLVED",
                                          "No se pudo resolver inequívocamente el participante"))
                continue
            if session.status is not ResolutionStatus.RESOLVED or not session.internal_id:
                issues.append(ModuleIssue(ModuleIssueStatus.NEEDS_REVIEW, "SESSION_NOT_RESOLVED",
                                          "No se pudo resolver inequívocamente la sesión",
                                          participant.internal_id))
                continue
            participant_id = participant.internal_id
            session_id = session.internal_id
            applicability = self.applicability_resolver.is_applicable(participant_id, session_id)
            if applicability.status is ApplicabilityStatus.UNKNOWN:
                issues.append(ModuleIssue(ModuleIssueStatus.NEEDS_REVIEW, "APPLICABILITY_UNKNOWN",
                                          "La aplicabilidad de la sesión es desconocida", participant_id, session_id))
                continue
            for canonical_metric, source_metric in self.config.metric_sources.items():
                key = (participant_external_id, session_external_id, source_metric)
                if key in duplicate_keys:
                    continue
                source_fact = by_key.get(key)
                provenance = _provenance(source_fact)
                if applicability.status is ApplicabilityStatus.NOT_APPLICABLE:
                    observations.append(Observation(participant_id, session_id, "attendance", canonical_metric,
                                                    None, ObservationStatus.NOT_APPLICABLE, provenance))
                    continue
                if source_fact is None:
                    if key[:2] in pair_has_row or (
                        data.coverage is SourceCoverage.EXHAUSTIVE and key[:2] in expected
                    ):
                        observations.append(Observation(participant_id, session_id, "attendance", canonical_metric,
                                                        None, ObservationStatus.NO_DATA, ()))
                    continue
                if source_fact.availability is AvailabilityStatus.AVAILABLE:
                    observations.append(Observation(participant_id, session_id, "attendance", canonical_metric,
                                                    source_fact.value, ObservationStatus.OBSERVED, provenance))
                elif source_fact.availability is AvailabilityStatus.MISSING:
                    observations.append(Observation(participant_id, session_id, "attendance", canonical_metric,
                                                    None, ObservationStatus.NO_DATA, provenance))
                else:
                    issues.append(ModuleIssue(ModuleIssueStatus.INVALID, "INVALID_FACT",
                                              "El CanonicalFact no tiene un valor utilizable", participant_id, session_id))
                    observations.append(Observation(participant_id, session_id, "attendance", canonical_metric,
                                                    None, ObservationStatus.INCOMPLETE, provenance))
        return ModuleResult(tuple(observations), tuple(issues))


def _provenance(fact: CanonicalFact | None) -> tuple[ObservationProvenance, ...]:
    if fact is None:
        return ()
    source = fact.provenance
    locator = ":".join(item for item in (source.sheet_name, str(source.row_number), source.column_name) if item)
    return (ObservationProvenance(source.file_name, locator or None),)
