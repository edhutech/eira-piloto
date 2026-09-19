"""Shared contracts for semantic modules."""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ...core.observation import Observation


class ModuleIssueStatus(str, Enum):
    NEEDS_REVIEW = "NEEDS_REVIEW"
    INVALID = "INVALID"


@dataclass(frozen=True)
class ModuleIssue:
    status: ModuleIssueStatus
    code: str
    message: str
    participant_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class ModuleResult:
    observations: tuple["Observation", ...]
    issues: tuple[ModuleIssue, ...]


class SourceCoverage(str, Enum):
    EXHAUSTIVE = "EXHAUSTIVE"
    SPARSE = "SPARSE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ExternalPair:
    participant_external_id: str
    session_external_id: str


@dataclass(frozen=True)
class ParticipantSessionPair:
    participant_id: str
    session_external_id: str


class ApplicabilityStatus(str, Enum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ApplicabilityResult:
    status: ApplicabilityStatus


class ApplicabilityResolver(Protocol):
    def is_applicable(self, participant_id: str, session_id: str) -> ApplicabilityResult: ...
