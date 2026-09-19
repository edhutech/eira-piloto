"""Contracts for mapping external structured data into canonical ingestion facts."""

from .mapping import ExternalDataMapping, FieldMapping, MappingResult, MappingStatus, map_table
from .models import AvailabilityStatus, CanonicalFact, SourceProvenance, TabularRow, TabularTable
from .validation import ValidationCode, ValidationIssue

__all__ = [
    "AvailabilityStatus",
    "CanonicalFact",
    "ExternalDataMapping",
    "FieldMapping",
    "MappingResult",
    "MappingStatus",
    "SourceProvenance",
    "TabularRow",
    "TabularTable",
    "ValidationCode",
    "ValidationIssue",
    "map_table",
]
