from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Protocol

from ...core.participants import (ParticipantResolver, ResolutionResult as CoreResolutionResult,
                                  ResolutionStatus as ParticipantResolutionStatus, strict_name_key)
class ResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NOT_FOUND = "NOT_FOUND"
    IGNORED = "IGNORED"


@dataclass(frozen=True)
class ResolutionResult:
    status: ResolutionStatus
    internal_id: str | None = None
    reason: str = ""
    provenance: str = ""


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
class KnownExternalIdentity:
    observed_name: str
    reason: str
    provenance: str


@dataclass
class EventIdentityResolver:
    """Application-layer identity policy for explicit external identities."""

    resolver: ParticipantResolver
    known_external: Mapping[str, KnownExternalIdentity] = field(default_factory=dict)

    def resolve_event(self, event: object) -> ResolutionResult | CoreResolutionResult:
        if getattr(event, "identity_type", "HUMAN") == "SYSTEM":
            return ResolutionResult(ResolutionStatus.IGNORED, reason="SYSTEM", provenance="event")
        observed = str(getattr(event, "participant_raw", ""))
        external = self.known_external.get(strict_name_key(observed))
        if external is not None:
            return ResolutionResult(ResolutionStatus.IGNORED,
                                    reason=external.reason, provenance=external.provenance)
        return self.resolver.resolve_event(event)


@dataclass(frozen=True)
class AlwaysApplicable:
    def is_applicable(self, participant_id: str, session_id: str):
        from .models import ApplicabilityResult, ApplicabilityStatus
        return ApplicabilityResult(ApplicabilityStatus.APPLICABLE)
