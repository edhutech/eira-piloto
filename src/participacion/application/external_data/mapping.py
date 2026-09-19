from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping

from .models import AvailabilityStatus, CanonicalFact, SourceProvenance, TabularTable
from .validation import ValidationCode, ValidationIssue, validate_mapping_structure


@dataclass(frozen=True)
class FieldMapping:
    column: str
    value_type: str
    required: bool = False


@dataclass(frozen=True)
class ExternalDataMapping:
    participant: FieldMapping
    session: FieldMapping
    metrics: Mapping[str, FieldMapping]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalDataMapping":
        return cls(
            participant=_field(value["participant_external_id"]),
            session=_field(value["session_external_id"]),
            metrics={name: _field(item) for name, item in value.get("metrics", {}).items()},
        )


class MappingStatus(str, Enum):
    VALID = "VALID"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    INVALID = "INVALID"


@dataclass(frozen=True)
class MappingResult:
    status: MappingStatus
    facts: tuple[CanonicalFact, ...]
    issues: tuple[ValidationIssue, ...]


def map_table(table: TabularTable, mapping: ExternalDataMapping) -> MappingResult:
    structural_issues = validate_mapping_structure(table, mapping)
    if structural_issues:
        return MappingResult(MappingStatus.INVALID, (), tuple(structural_issues))

    facts: list[CanonicalFact] = []
    issues: list[ValidationIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for row in table.rows:
        participant = _identity_value(row.values.get(mapping.participant.column))
        session = _identity_value(row.values.get(mapping.session.column))
        if not participant or not session:
            issues.append(ValidationIssue(
                ValidationCode.INCOMPLETE_ROW,
                "La fila requiere identidad externa de participante y sesión",
                row.row_number,
            ))
            continue
        for metric, field in mapping.metrics.items():
            raw = row.values.get(field.column)
            provenance = SourceProvenance(table.source_name, table.sheet_name, row.row_number, field.column)
            key = (participant, session, metric)
            duplicate = key in seen
            seen.add(key)
            if _is_empty(raw):
                if field.required:
                    issues.append(ValidationIssue(
                        ValidationCode.INCOMPLETE_ROW,
                        "Falta un valor requerido",
                        row.row_number,
                        field.column,
                        metric,
                    ))
                facts.append(CanonicalFact(participant, session, metric, None,
                                           AvailabilityStatus.MISSING, provenance))
                continue
            try:
                value = _convert(raw, field.value_type)
            except (TypeError, ValueError, InvalidOperation):
                issues.append(ValidationIssue(
                    ValidationCode.INVALID_VALUE,
                    f"Valor incompatible con el tipo {field.value_type}",
                    row.row_number,
                    field.column,
                    metric,
                ))
                facts.append(CanonicalFact(participant, session, metric, None,
                                           AvailabilityStatus.INVALID, provenance))
                continue
            if duplicate:
                issues.append(ValidationIssue(
                    ValidationCode.DUPLICATE_FACT,
                    "Existe más de un hecho para la misma identidad, sesión y métrica",
                    row.row_number,
                    field.column,
                    metric,
                ))
            facts.append(CanonicalFact(participant, session, metric, value,
                                       AvailabilityStatus.AVAILABLE, provenance))
    status = MappingStatus.NEEDS_REVIEW if issues else MappingStatus.VALID
    return MappingResult(status, tuple(facts), tuple(issues))


def _field(value: Mapping[str, Any]) -> FieldMapping:
    return FieldMapping(str(value["column"]), str(value["type"]), bool(value.get("required", False)))


def _identity_value(value: Any) -> str:
    if _is_empty(value):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _convert(value: Any, value_type: str) -> Any:
    if value_type == "string":
        return str(value).strip()
    if value_type == "integer":
        if isinstance(value, bool):
            raise ValueError("boolean no es entero")
        if isinstance(value, int):
            return value
        number = Decimal(str(value).strip())
        if number != number.to_integral_value():
            raise ValueError("no es entero")
        return int(number)
    if value_type == "number":
        if isinstance(value, bool):
            raise ValueError("boolean no es número")
        return float(Decimal(str(value).strip()))
    if value_type == "ratio":
        number = float(Decimal(str(value).strip()))
        if not 0 <= number <= 1:
            raise ValueError("ratio fuera de rango")
        return number
    if value_type == "boolean":
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().casefold()
        if normalized in {"true", "1", "yes", "si", "sí"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
        raise ValueError("boolean inválido")
    if value_type == "date":
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value).strip())
    raise ValueError(f"tipo no soportado: {value_type}")
