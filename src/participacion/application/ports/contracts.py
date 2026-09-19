"""Small provider-neutral contracts used by application services."""
from __future__ import annotations

from typing import Any, Protocol, Sequence

from ...core.models import ParticipantSnapshot, SourceArtifact
from ..external_data.models import TabularTable
from ...core.scoring import ParticipantSessionScore


class ContentReader(Protocol):
    def read(self, artifact: SourceArtifact) -> Any: ...


class TabularReader(Protocol):
    def read(self, source: str, **options: Any) -> TabularTable: ...


class StateStore(Protocol):
    def load(self) -> dict[str, Any]: ...
    def save(self, state: dict[str, Any]) -> None: ...


class SessionResultsStore(Protocol):
    def load_scores(self) -> list[Any]: ...
    def replace_session(self, session_number: int, session_name: str,
                        scores: Sequence[ParticipantSessionScore],
                        participant_snapshots: dict[str, ParticipantSnapshot],
                        *, ruleset_version: int = 1) -> Any: ...
