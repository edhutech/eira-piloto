from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol

from ...core.observation import Observation, ObservationProvenance, ObservationStatus
from ...core.scoring import ParticipantSessionScore
from .models import (
    ApplicabilityResolver,
    ApplicabilityStatus,
    ModuleIssue,
    ModuleIssueStatus,
    ModuleResult,
    ParticipantSessionPair,
    SourceCoverage,
)
from .resolution import ResolutionStatus


class SessionNumberResolver(Protocol):
    def resolve(self, external_id: str) -> object: ...


@dataclass(frozen=True)
class ParticipationConfig:
    source_id: str
    additional_metrics: tuple[str, ...] = ()
    coverage: SourceCoverage = SourceCoverage.UNKNOWN

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("ParticipationConfig requiere source_id")
        allowed = {"participation_score", "voice_valid", "chat_valid", "ambiguous_total"}
        if any(metric not in allowed for metric in self.metrics):
            raise ValueError("Métrica de participación no soportada")

    @property
    def metrics(self) -> tuple[str, ...]:
        return ("participation_score", *self.additional_metrics)


class ParticipationModule:
    def __init__(self, session_resolver: object, applicability_resolver: ApplicabilityResolver,
                 config: ParticipationConfig):
        self.session_resolver = session_resolver
        self.applicability_resolver = applicability_resolver
        self.config = config

    def build_observations(
        self,
        scores: Iterable[ParticipantSessionScore],
        *,
        expected_pairs: Iterable[ParticipantSessionPair] = (),
    ) -> ModuleResult:
        issues: list[ModuleIssue] = []
        indexed: dict[tuple[str, int], ParticipantSessionScore] = {}
        duplicate_keys: set[tuple[str, int]] = set()
        for score in scores:
            if not score.participant_id.strip():
                issues.append(ModuleIssue(ModuleIssueStatus.INVALID, "MISSING_PARTICIPANT_ID",
                                          "El score no tiene participant_id"))
                continue
            key = (score.participant_id, score.session_number)
            if key in indexed:
                duplicate_keys.add(key)
                issues.append(ModuleIssue(ModuleIssueStatus.INVALID, "DUPLICATE_SCORE",
                                          "Existe más de un score para participante y sesión",
                                          participant_id=score.participant_id))
            else:
                indexed[key] = score
        pairs = list(expected_pairs)
        expected_keys: set[tuple[str, int]] = set()
        for pair in pairs:
            try:
                number = int(pair.session_external_id)
            except ValueError:
                issues.append(ModuleIssue(ModuleIssueStatus.NEEDS_REVIEW, "INVALID_SESSION_REFERENCE",
                                          "La sesión esperada no es resoluble", pair.participant_id))
                continue
            key = (pair.participant_id, number)
            if key in expected_keys:
                issues.append(ModuleIssue(ModuleIssueStatus.INVALID, "DUPLICATE_EXPECTED_PAIR",
                                          "Existe más de un par esperado", pair.participant_id))
            expected_keys.add(key)
        keys = set(indexed) | expected_keys
        observations: list[Observation] = []
        for participant_id, session_number in sorted(keys):
            if (participant_id, session_number) in duplicate_keys:
                continue
            session_result = self._resolve_session(session_number)
            if session_result is None:
                issues.append(ModuleIssue(ModuleIssueStatus.NEEDS_REVIEW, "SESSION_NOT_RESOLVED",
                                          "No se pudo resolver la sesión", participant_id))
                continue
            session_id = session_result
            applicability = self.applicability_resolver.is_applicable(participant_id, session_id)
            if applicability.status is ApplicabilityStatus.UNKNOWN:
                issues.append(ModuleIssue(ModuleIssueStatus.NEEDS_REVIEW, "APPLICABILITY_UNKNOWN",
                                          "La aplicabilidad de la sesión es desconocida", participant_id, session_id))
                continue
            score = indexed.get((participant_id, session_number))
            provenance = (ObservationProvenance(self.config.source_id, f"session:{session_id}/participant:{participant_id}"),)
            if applicability.status is ApplicabilityStatus.NOT_APPLICABLE:
                observations.extend(self._observations(participant_id, session_id, None,
                                                       ObservationStatus.NOT_APPLICABLE, provenance))
            elif score is None:
                if self.config.coverage is SourceCoverage.EXHAUSTIVE:
                    observations.extend(self._observations(participant_id, session_id, None,
                                                           ObservationStatus.NO_DATA, provenance))
            else:
                status = ObservationStatus.OBSERVED if score.scoring_complete else ObservationStatus.INCOMPLETE
                observations.extend(self._observations(participant_id, session_id, score, status, provenance))
        return ModuleResult(tuple(observations), tuple(issues))

    def _resolve_session(self, session_number: int) -> str | None:
        resolver = self.session_resolver
        result = resolver.resolve(str(session_number)) if hasattr(resolver, "resolve") else None
        if result is None:
            return None
        status = getattr(result, "status", None)
        if status is ResolutionStatus.RESOLVED:
            return str(getattr(result, "internal_id", "")) or None
        return None

    def _observations(self, participant_id: str, session_id: str, score: ParticipantSessionScore | None,
                      status: ObservationStatus, provenance: tuple[ObservationProvenance, ...]) -> list[Observation]:
        values: Mapping[str, object | None] = {
            "participation_score": score.score if score else None,
            "voice_valid": score.voice_valid if score else None,
            "chat_valid": score.chat_valid if score else None,
            "ambiguous_total": score.ambiguous_total if score else None,
        }
        return [Observation(participant_id, session_id, "participation", metric,
                            values[metric] if status is ObservationStatus.OBSERVED else None,
                            status, provenance) for metric in self.config.metrics]
