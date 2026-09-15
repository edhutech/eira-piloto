from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .participant_repository import ParticipantRepository
from .participants import Participant, strict_name_key

WAITING_FOR_OFFICIAL_ROSTER = "WAITING_FOR_OFFICIAL_ROSTER"


@dataclass(frozen=True)
class RosterRecord:
    nombre: str
    correo: str
    role: str | None = None
    enrollment_status: str = "active"
    start_session: int = 1
    end_session: int | None = None


class RosterAction(str):
    MATCH = "MATCH"
    UPDATE = "UPDATE"
    CREATE = "CREATE"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass(frozen=True)
class RosterPlanItem:
    source: RosterRecord
    action: str
    participant_id: str | None
    candidates: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class RosterPlan:
    items: tuple[RosterPlanItem, ...]

    @property
    def needs_review(self) -> tuple[RosterPlanItem, ...]:
        return tuple(item for item in self.items if item.action == RosterAction.NEEDS_REVIEW)

    @property
    def creates(self) -> tuple[RosterPlanItem, ...]:
        return tuple(item for item in self.items if item.action == RosterAction.CREATE)


class GoogleRosterSource(Protocol):
    def read_values(self) -> list[list[Any]]: ...


class RosterImporter:
    """Non-destructive official roster reconciliation.

    Planning is always read-only. Applying requires an explicit call to apply(plan).
    """

    def __init__(self, participant_repository: ParticipantRepository):
        self.repository = participant_repository

    def dry_run(self, records: Iterable[RosterRecord]) -> RosterPlan:
        existing = self.repository.load()
        people = list(existing)
        items: list[RosterPlanItem] = []
        for record in records:
            matches, method = _matches(record, people)
            if len(matches) > 1:
                items.append(RosterPlanItem(record, RosterAction.NEEDS_REVIEW, None,
                                            tuple(p.participant_id for p in matches),
                                            f"match ambiguo por {method}"))
                continue
            if matches:
                person = matches[0]
                changed = _needs_update(person, record)
                items.append(RosterPlanItem(record, RosterAction.UPDATE if changed else RosterAction.MATCH,
                                            person.participant_id, reason=method))
                continue
            participant_id = _stable_official_id(record)
            items.append(RosterPlanItem(record, RosterAction.CREATE, participant_id,
                                        reason="sin candidato existente"))
        return RosterPlan(tuple(items))

    def apply(self, plan: RosterPlan) -> RosterPlan:
        if plan.needs_review:
            raise ValueError("El roster contiene reconciliaciones NEEDS_REVIEW; no se aplica automáticamente")
        existing = {p.participant_id: p for p in self.repository.load()}
        new_people: list[Participant] = []
        updates: dict[str, dict[str, Any]] = {}
        for item in plan.items:
            record = item.source
            if item.action == RosterAction.CREATE:
                new_people.append(Participant(
                    item.participant_id or _stable_official_id(record), record.nombre, record.correo,
                    [], "participant", "official", "unverified", record.enrollment_status,
                    record.start_session, record.end_session,
                ))
            elif item.action == RosterAction.UPDATE and item.participant_id in existing:
                updates[item.participant_id] = {
                    "nombre": record.nombre, "correo": record.correo,
                    "enrollment_status": record.enrollment_status,
                    "start_session": record.start_session,
                    "end_session": record.end_session if record.end_session is not None else "",
                }
        if new_people:
            self.repository.upsert(new_people)
        if updates:
            self.repository.update_fields(updates)
        return plan


def _matches(record: RosterRecord, people: Sequence[Participant]) -> tuple[list[Participant], str]:
    email = record.correo.strip().casefold()
    if email:
        candidates = [p for p in people if p.correo.strip().casefold() == email]
        if candidates:
            return candidates, "email exacto"
    name = strict_name_key(record.nombre)
    candidates = [p for p in people if strict_name_key(p.nombre) == name]
    if candidates:
        return candidates, "nombre exacto"
    candidates = [p for p in people if any(strict_name_key(alias) == name for alias in p.aliases or [])]
    return candidates, "alias exacto"


def _needs_update(person: Participant, record: RosterRecord) -> bool:
    return (person.nombre != record.nombre or person.correo != record.correo or
            person.enrollment_status != record.enrollment_status or
            person.start_session != record.start_session or person.end_session != record.end_session)


def _stable_official_id(record: RosterRecord) -> str:
    key = record.correo.strip().casefold() or strict_name_key(record.nombre)
    return "participant_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def read_csv_roster(path: str | Path) -> list[RosterRecord]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        return [_record_from_mapping(row) for row in rows]


def read_xlsx_roster(path: str | Path) -> list[RosterRecord]:
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("XLSX requiere openpyxl instalado en el entorno del proyecto") from exc
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(value or "").strip() for value in rows[0]]
    return [_record_from_mapping(dict(zip(headers, row))) for row in rows[1:] if any(row)]


def read_google_roster(source: GoogleRosterSource) -> list[RosterRecord]:
    values = source.read_values()
    if not values:
        return []
    headers = [str(value or "").strip() for value in values[0]]
    return [_record_from_mapping(dict(zip(headers, row))) for row in values[1:] if any(row)]


def _record_from_mapping(row: Mapping[str, Any]) -> RosterRecord:
    normalized = {str(key).strip().casefold().replace(" ", "_"): value for key, value in row.items()}
    name = str(normalized.get("nombre", normalized.get("name", "")) or "").strip()
    email = str(normalized.get("correo", normalized.get("email", "")) or "").strip()
    if not name or not email:
        raise ValueError("Cada fila del roster requiere nombre y correo")
    end = normalized.get("end_session", "")
    return RosterRecord(
        name, email,
        str(normalized.get("role", "") or "").strip() or None,
        str(normalized.get("enrollment_status", "active") or "active").strip(),
        int(float(normalized.get("start_session", 1) or 1)),
        None if str(end or "").strip() == "" else int(float(end)),
    )
