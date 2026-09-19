from __future__ import annotations

from typing import Protocol

from ...core.models import EvidenceSourceRef, ResolvedEvidence


class EvidenceResolver(Protocol):
    """Neutral application port for resolving declared evidence references."""

    def resolve(self, sources: tuple[EvidenceSourceRef, ...]) -> list[ResolvedEvidence]: ...
