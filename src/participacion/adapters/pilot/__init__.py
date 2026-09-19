"""Generic adapters used by the read-only pilot composition."""

from .google_participation_source import ReadOnlyGoogleParticipationSource, build_participant_resolver

__all__ = ["ReadOnlyGoogleParticipationSource", "build_participant_resolver"]
