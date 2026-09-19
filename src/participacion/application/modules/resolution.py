from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from ...core.participants import ParticipantResolver, ResolutionStatus as ParticipantResolutionStatus
class ResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class ResolutionResult:
    status: ResolutionStatus
    internal_id: str | None = None


class ExternalParticipantResolver(Protocol):
    def resolve(self, external_id: str) -> ResolutionResult: ...


class ExternalSessionResolver(Protocol):
    def resolve(self, external_id: str) -> ResolutionResult: ...


@dataclass(frozen=True)
class MappingSessionResolver:
    mapping: Mapping[str, str]

    def resolve(self, external_id: str) -> ResolutionResult:
        internal_id = self.mapping.get(external_id)
        return (ResolutionResult(ResolutionStatus.RESOLVED, internal_id)
                if internal_id else ResolutionResult(ResolutionStatus.NOT_FOUND))


@dataclass
class ParticipantResolverAdapter:
    resolver: ParticipantResolver
    match_email: bool = False

    def resolve(self, external_id: str) -> ResolutionResult:
        result = self.resolver.resolve(
            external_id,
            email=external_id if self.match_email else None,
        )
        if result.status is ParticipantResolutionStatus.RESOLVED and result.participant_id:
            return ResolutionResult(ResolutionStatus.RESOLVED, result.participant_id)
        if result.status is ParticipantResolutionStatus.NEEDS_REVIEW:
            return ResolutionResult(ResolutionStatus.NEEDS_REVIEW)
        return ResolutionResult(ResolutionStatus.NOT_FOUND)


@dataclass(frozen=True)
class AlwaysApplicable:
    def is_applicable(self, participant_id: str, session_id: str):
        from .models import ApplicabilityResult, ApplicabilityStatus
        return ApplicabilityResult(ApplicabilityStatus.APPLICABLE)
