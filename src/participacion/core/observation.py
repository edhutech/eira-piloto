from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ObservationStatus(str, Enum):
    OBSERVED = "OBSERVED"
    NO_DATA = "NO_DATA"
    INCOMPLETE = "INCOMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class ObservationProvenance:
    source_id: str
    locator: str | None = None


@dataclass(frozen=True)
class Observation:
    participant_id: str
    session_id: str
    dimension: str
    metric: str
    value: Any | None
    status: ObservationStatus
    provenance: tuple[ObservationProvenance, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", ObservationStatus(self.status))
        for field_name in ("participant_id", "session_id", "dimension", "metric"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} no puede estar vacío")
        if self.status is ObservationStatus.OBSERVED and self.value is None:
            raise ValueError("OBSERVED requiere un valor")
        if self.status is not ObservationStatus.OBSERVED and self.value is not None:
            raise ValueError(f"{self.status.value} no puede transportar un valor")
        if any(not item.source_id.strip() for item in self.provenance):
            raise ValueError("Cada provenance requiere source_id")
