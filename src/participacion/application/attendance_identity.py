from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .modules.resolution import ExternalParticipantResolver, ResolutionResult, ResolutionStatus

_REQUIRED_COLUMNS = {
    "external_id", "status", "canonical_external_id", "reason", "provenance",
}


def email_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


@dataclass(frozen=True)
class AttendanceIdentityRule:
    external_id: str
    status: ResolutionStatus
    canonical_external_id: str = ""
    reason: str = ""
    provenance: str = ""


@dataclass(frozen=True)
class AttendanceIdentityPolicy:
    resolver: ExternalParticipantResolver
    rules: Mapping[str, AttendanceIdentityRule]

    def resolve(self, external_id: str) -> ResolutionResult:
        observed = email_key(external_id)
        rule = self.rules.get(observed)
        if rule is None:
            return self.resolver.resolve(observed)
        if rule.status is ResolutionStatus.IGNORED:
            return ResolutionResult(ResolutionStatus.IGNORED, reason=rule.reason,
                                    provenance=rule.provenance)
        return self.resolver.resolve(rule.canonical_external_id)


def attendance_identity_policy_from_rows(
    rows: Iterable[Mapping[str, object]],
    resolver: ExternalParticipantResolver,
) -> AttendanceIdentityPolicy:
    rules: dict[str, AttendanceIdentityRule] = {}
    for row_number, row in enumerate(rows, 2):
        observed = email_key(str(row.get("external_id", "") or ""))
        if not observed:
            raise ValueError(f"Attendance identity config fila {row_number}: external_id vacío")
        try:
            status = ResolutionStatus(str(row.get("status", "") or "").strip().upper())
        except ValueError as exc:
            raise ValueError(f"Attendance identity config fila {row_number}: status inválido") from exc
        canonical = email_key(str(row.get("canonical_external_id", "") or ""))
        if status is ResolutionStatus.RESOLVED and not canonical:
            raise ValueError(
                f"Attendance identity config fila {row_number}: RESOLVED requiere canonical_external_id"
            )
        if status not in {ResolutionStatus.RESOLVED, ResolutionStatus.IGNORED}:
            raise ValueError(f"Attendance identity config fila {row_number}: status inválido")
        rule = AttendanceIdentityRule(
            observed,
            status,
            canonical,
            str(row.get("reason", "") or "").strip(),
            str(row.get("provenance", "") or "").strip(),
        )
        previous = rules.get(observed)
        if previous is not None and previous != rule:
            raise ValueError(
                f"Attendance identity config fila {row_number}: clave duplicada/conflictiva"
            )
        rules[observed] = rule
    return AttendanceIdentityPolicy(resolver, rules)


def load_attendance_identity_policy(
    path: str | Path,
    resolver: ExternalParticipantResolver,
) -> AttendanceIdentityPolicy:
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError(f"Attendance identity config no existe: {file_path}")
    with file_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or ()) != _REQUIRED_COLUMNS:
            raise ValueError("Attendance identity config requiere columnas exactas: "
                             + ", ".join(sorted(_REQUIRED_COLUMNS)))
        return attendance_identity_policy_from_rows(reader, resolver)
