"""Small provider-neutral contracts used by application services."""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from ...core.models import ContentArtifact, ProgramInspection, ProgramRecord, SessionRecord, SourceArtifact


class SessionSource(Protocol):
    def inspect(self, program: ProgramRecord, state: Mapping[str, Any], program_id: str) -> ProgramInspection: ...


class ContentReader(Protocol):
    def read(self, artifact: SourceArtifact) -> ContentArtifact: ...


class StateStore(Protocol):
    def load(self) -> dict[str, Any]: ...
    def save(self, state: dict[str, Any]) -> None: ...


class ParticipantStore(Protocol):
    def load(self) -> list[Any]: ...


class SessionResultsStore(Protocol):
    def load_scores(self) -> list[Any]: ...


class ViewStore(Protocol):
    def persist(self, value: Any, *args: Any, **kwargs: Any) -> Any: ...
