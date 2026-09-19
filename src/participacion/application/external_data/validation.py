from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .mapping import ExternalDataMapping
    from .models import TabularTable


class ValidationCode(str, Enum):
    MISSING_COLUMN = "MISSING_COLUMN"
    INVALID_VALUE = "INVALID_VALUE"
    INCOMPLETE_ROW = "INCOMPLETE_ROW"
    DUPLICATE_FACT = "DUPLICATE_FACT"
    AMBIGUOUS_MAPPING = "AMBIGUOUS_MAPPING"


@dataclass(frozen=True)
class ValidationIssue:
    code: ValidationCode
    message: str
    row_number: int | None = None
    column_name: str | None = None
    metric: str | None = None


def validate_mapping_structure(table: "TabularTable", mapping: "ExternalDataMapping") -> list[ValidationIssue]:
    columns = set(table.columns)
    fields = [mapping.participant, mapping.session, *mapping.metrics.values()]
    issues: list[ValidationIssue] = []
    for field in fields:
        if field.column not in columns:
            issues.append(ValidationIssue(
                ValidationCode.MISSING_COLUMN,
                f"No existe la columna configurada: {field.column}",
                column_name=field.column,
            ))
    by_column: dict[str, list[str]] = {}
    by_column.setdefault(mapping.participant.column, []).append("participant_external_id")
    by_column.setdefault(mapping.session.column, []).append("session_external_id")
    for metric, field in mapping.metrics.items():
        by_column.setdefault(field.column, []).append(metric)
    for column, names in by_column.items():
        if len(names) > 1:
            issues.append(ValidationIssue(
                ValidationCode.AMBIGUOUS_MAPPING,
                f"La columna {column} está asignada a varios campos: {', '.join(names)}",
                column_name=column,
            ))
    return issues
