from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence, cast

from ..core.participants import Participant, Role, strict_name_key
from .aliases import decode_aliases, encode_aliases

WAITING_FOR_OFFICIAL_ROSTER = "WAITING_FOR_OFFICIAL_ROSTER"


@dataclass(frozen=True)
class RosterRecord:
    nombre: str
    correo: str
    role: str | None = None
    enrollment_status: str = "active"
    start_session: int = 1
    end_session: int | None = None
    participant_id: str | None = None
    aliases: tuple[str, ...] = ()


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


class RosterImporter:
    """Non-destructive official roster reconciliation.

    The roster is authoritative for identity and enrollment fields only;
    ``role`` remains a manually protected participant field. Planning is
    always read-only. Applying requires an explicit call to apply(plan).
    """

    def __init__(self, participant_repository: Any):
        self.repository = participant_repository

    def dry_run(self, records: Iterable[RosterRecord]) -> RosterPlan:
        read = getattr(self.repository, "load_read_only", self.repository.load)
        existing = read()
        people = list(existing)
        records = list(records)
        duplicate_emails = _duplicate_roster_emails(records)
        planned_ids: dict[str, RosterRecord] = {}
        items: list[RosterPlanItem] = []
        for record in records:
            email_key = record.correo.strip().casefold()
            if email_key in duplicate_emails:
                items.append(RosterPlanItem(record, RosterAction.NEEDS_REVIEW, None,
                                            reason="correo repetido dentro del roster"))
                continue
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
            existing_id = next((p for p in people if p.participant_id == participant_id), None)
            planned_record = planned_ids.get(participant_id)
            if existing_id is not None or planned_record is not None:
                reason = ("participant_id oficial colisiona con una identidad existente"
                          if existing_id is not None else
                          "participant_id oficial colisiona dentro del roster")
                items.append(RosterPlanItem(record, RosterAction.NEEDS_REVIEW, participant_id,
                                            reason=reason))
                continue
            planned_ids[participant_id] = record
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
                    list(record.aliases), cast(Role, "participant"), "official", "unverified", record.enrollment_status,
                    record.start_session, record.end_session,
                ))
            elif item.action == RosterAction.UPDATE and item.participant_id in existing:
                participant_id = item.participant_id
                assert participant_id is not None
                updates[participant_id] = {
                    "nombre": record.nombre, "correo": record.correo,
                    "aliases": encode_aliases(record.aliases),
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


def _duplicate_roster_emails(records: Sequence[RosterRecord]) -> set[str]:
    counts: dict[str, int] = {}
    for record in records:
        key = record.correo.strip().casefold()
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if key and count > 1}


def _needs_update(person: Participant, record: RosterRecord) -> bool:
    return (person.nombre != record.nombre or person.correo != record.correo or
            {strict_name_key(value) for value in person.aliases or []} !=
            {strict_name_key(value) for value in record.aliases} or
            person.enrollment_status != record.enrollment_status or
            person.start_session != record.start_session or person.end_session != record.end_session)


def _stable_official_id(record: RosterRecord) -> str:
    key = record.correo.strip().casefold() or strict_name_key(record.nombre)
    return "participant_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def roster_records_from_rows(rows: Sequence[Sequence[Any]]) -> list[RosterRecord]:
    if not rows:
        return []
    headers = [str(value or "").strip() for value in rows[0]]
    roster_header_positions(headers)
    has_participant_id = "participant_id" in {
        header.strip().casefold().replace(" ", "_") for header in headers
    }
    records = [_record_from_mapping(dict(zip(headers, row))) for row in rows[1:] if any(row)]
    if has_participant_id:
        ids = [record.participant_id for record in records]
        if any(not value or not str(value).strip() for value in ids):
            raise ValueError("participant_id no puede estar vacío cuando la columna existe")
        if len(set(ids)) != len(ids):
            raise ValueError("participant_id duplicado dentro del roster")
    return records


def roster_header_positions(headers: Sequence[Any]) -> dict[str, int]:
    positions = {str(header).strip().casefold().replace(" ", "_"): index
                 for index, header in enumerate(headers)}
    if "nombre" not in positions and "name" in positions:
        positions["nombre"] = positions["name"]
    if "correo" not in positions and "email" in positions:
        positions["correo"] = positions["email"]
    missing = [key for key in ("nombre", "correo") if key not in positions]
    if missing:
        raise ValueError("Faltan columnas obligatorias: " + ", ".join(missing))
    return {key: positions[key] for key in ("nombre", "correo")}


def roster_records_to_participants(records: Iterable[RosterRecord], source: str) -> list[dict[str, str]]:
    """Adapt canonical roster records to the initialization sheet contract."""
    return [{
        "participant_id": record.participant_id or "", "nombre": record.nombre, "correo": record.correo,
        "aliases": encode_aliases(record.aliases), "role": record.role or "participant", "source": source, "status": "new",
        "enrollment_status": record.enrollment_status,
        "start_session": str(record.start_session),
        "end_session": "" if record.end_session is None else str(record.end_session),
    } for record in records]


def _record_from_mapping(row: Mapping[str, Any]) -> RosterRecord:
    normalized = {str(key).strip().casefold().replace(" ", "_"): value for key, value in row.items()}
    name = str(normalized.get("nombre", normalized.get("name", "")) or "").strip()
    email = str(normalized.get("correo", normalized.get("email", "")) or "").strip()
    if not name or not email:
        raise ValueError("Cada fila del roster requiere nombre y correo")
    end = normalized.get("end_session", "")
    return RosterRecord(
        nombre=name,
        correo=email,
        role=str(normalized.get("role", "") or "").strip() or None,
        enrollment_status=str(normalized.get("enrollment_status", "active") or "active").strip(),
        start_session=int(float(normalized.get("start_session", 1) or 1)),
        end_session=None if str(end or "").strip() == "" else int(float(end)),
        participant_id=str(normalized.get("participant_id", "") or "").strip() or None,
        aliases=tuple(decode_aliases(normalized.get("aliases", ""))),
    )
