from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Callable

from ...application.external_data.models import TabularTable
from ...application.modules.resolution import ResolutionStatus
from .results import OutcomeRecord


@dataclass(frozen=True)
class OutcomeMapping:
    participant_column: str
    date_column: str
    type_column: str


def map_outcomes(
    table: TabularTable,
    mapping: OutcomeMapping,
    resolve_participant: Callable[[str], object],
) -> tuple[tuple[OutcomeRecord, ...], tuple[str, ...]]:
    required = {mapping.participant_column, mapping.date_column, mapping.type_column}
    missing = required.difference(table.columns)
    if missing:
        raise ValueError("Mapping de outcomes requiere columnas: " + ", ".join(sorted(missing)))
    outcomes: list[OutcomeRecord] = []
    issues: list[str] = []
    for row in table.rows:
        participant_external = str(row.values.get(mapping.participant_column, "")).strip()
        if not participant_external:
            issues.append(f"row:{row.row_number}:missing_participant")
            continue
        resolution = resolve_participant(participant_external)
        if getattr(resolution, "status", None) is not ResolutionStatus.RESOLVED:
            issues.append(f"row:{row.row_number}:participant_needs_review")
            continue
        participant_id = getattr(resolution, "internal_id", None)
        if not participant_id:
            issues.append(f"row:{row.row_number}:participant_needs_review")
            continue
        occurred_at = _parse_datetime(row.values.get(mapping.date_column))
        if occurred_at is None:
            issues.append(f"row:{row.row_number}:invalid_date")
            continue
        outcome_type = str(row.values.get(mapping.type_column, "")).strip()
        if not outcome_type:
            issues.append(f"row:{row.row_number}:missing_type")
            continue
        outcomes.append(OutcomeRecord(participant_id, occurred_at, outcome_type, f"{table.source_name}:{table.sheet_name or ''}:{row.row_number}"))
    return tuple(outcomes), tuple(issues)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
