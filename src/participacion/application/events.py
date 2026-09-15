from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

from ..core.models import ProgramRecord


@dataclass(frozen=True)
class ApplicationEvent:
    event_type: str
    program_id: str | None = None
    program_name: str | None = None
    data: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProgramAddonContext:
    program: ProgramRecord
    session_statuses: Mapping[int, str]


@dataclass(frozen=True)
class AddonResult:
    addon_id: str
    changed: bool = False
    events: tuple[ApplicationEvent, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class ProgramAddon(Protocol):
    addon_id: str

    def run(self, context: ProgramAddonContext) -> AddonResult:
        raise NotImplementedError
