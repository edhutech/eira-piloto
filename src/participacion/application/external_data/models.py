from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class AvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    INVALID = "INVALID"


@dataclass(frozen=True)
class SourceProvenance:
    file_name: str
    sheet_name: str | None
    row_number: int
    column_name: str | None = None


@dataclass(frozen=True)
class CanonicalFact:
    participant_external_id: str
    session_external_id: str
    metric: str
    value: Any
    availability: AvailabilityStatus
    provenance: SourceProvenance


@dataclass(frozen=True)
class TabularRow:
    row_number: int
    values: Mapping[str, Any]


@dataclass(frozen=True)
class TabularTable:
    source_name: str
    sheet_name: str | None
    columns: tuple[str, ...]
    rows: tuple[TabularRow, ...]
