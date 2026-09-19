"""Semantic modules that translate source/domain outputs into Observations."""

from .attendance import AttendanceConfig, AttendanceInput, AttendanceModule
from .models import (
    ApplicabilityResult,
    ApplicabilityStatus,
    ExternalPair,
    ModuleIssue,
    ModuleIssueStatus,
    ModuleResult,
    ParticipantSessionPair,
    SourceCoverage,
)
from .participation import ParticipationConfig, ParticipationModule
from .resolution import (
    MappingSessionResolver,
    ParticipantResolverAdapter,
    ResolutionResult,
    ResolutionStatus,
)

__all__ = [
    "ApplicabilityResult",
    "ApplicabilityStatus",
    "AttendanceConfig",
    "AttendanceInput",
    "AttendanceModule",
    "ExternalPair",
    "MappingSessionResolver",
    "ModuleIssue",
    "ModuleIssueStatus",
    "ModuleResult",
    "ParticipantResolverAdapter",
    "ParticipantSessionPair",
    "ParticipationConfig",
    "ParticipationModule",
    "ResolutionResult",
    "ResolutionStatus",
    "SourceCoverage",
]
