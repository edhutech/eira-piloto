from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Literal


Role = Literal["participant", "facilitator", "other"]


class ResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    IGNORED = "IGNORED"


@dataclass
class Participant:
    participant_id: str
    nombre: str
    correo: str = ""
    aliases: list[str] | None = None
    role: Role = "participant"
    source: str = "auto"
    status: str = "unverified"
    enrollment_status: str = "active"
    start_session: int = 1
    end_session: int | None = None
    enrollment_config_valid: bool = True

    def __post_init__(self) -> None:
        self.aliases = list(self.aliases or [])
        if self.role not in {"participant", "facilitator", "other"}:
            raise ValueError(f"Rol de participante inválido: {self.role}")


@dataclass(frozen=True)
class ResolutionResult:
    status: ResolutionStatus
    participant: Participant | None = None

    @property
    def participant_id(self) -> str | None:
        return self.participant.participant_id if self.participant else None


@dataclass
class ParticipantResolver:
    mode: Literal["auto", "import", "official"]
    participants: list[Participant]

    @classmethod
    def auto(cls, records: Iterable[dict[str, Any]] = ()) -> "ParticipantResolver":
        return cls("auto", [_participant_from_record(record, "auto") for record in records])

    @classmethod
    def imported(cls, records: Iterable[dict[str, Any]]) -> "ParticipantResolver":
        return cls("import", [_participant_from_record(record, "import") for record in records])

    def resolve_event(self, event: Any) -> ResolutionResult:
        if getattr(event, "identity_type", "HUMAN") == "SYSTEM":
            return ResolutionResult(ResolutionStatus.IGNORED)
        return self.resolve(
            str(getattr(event, "participant_raw", "")),
            participant_id=getattr(event, "participant_id", None),
            email=getattr(event, "participant_email", None),
        )

    def resolve(
        self, participant_raw: str, *, participant_id: str | None = None, email: str | None = None
    ) -> ResolutionResult:
        if participant_id:
            candidates = [p for p in self.participants if p.participant_id == participant_id]
            if len(candidates) == 1:
                return ResolutionResult(ResolutionStatus.RESOLVED, candidates[0])
            if len(candidates) > 1:
                return ResolutionResult(ResolutionStatus.NEEDS_REVIEW)

        if email and email_key(email):
            result = self._unique(self._by_email(email_key(email)))
            if result:
                return result

        key = normalize_name(participant_raw)
        if not key:
            return ResolutionResult(ResolutionStatus.NEEDS_REVIEW)

        result = self._unique(self._by_alias(key, loose=False))
        if result:
            return result
        result = self._unique(self._by_name(key, loose=False))
        if result:
            return result

        if self.mode == "import":
            loose_key = loose_name_key(participant_raw)
            result = self._unique(self._by_alias(loose_key, loose=True))
            if result:
                return result
            result = self._unique(self._by_name(loose_key, loose=True))
            if result:
                return result

        if self.mode == "auto" and clearly_identifiable(participant_raw):
            participant = Participant(
                participant_id=auto_participant_id(),
                nombre=participant_raw.strip(),
                correo="",
                aliases=[],
                role="participant",
                source="auto",
                status="unverified",
            )
            self.participants.append(participant)
            return ResolutionResult(ResolutionStatus.RESOLVED, participant)

        return ResolutionResult(ResolutionStatus.NEEDS_REVIEW)

    def to_records(self) -> list[dict[str, Any]]:
        return [
            {
                "participant_id": p.participant_id,
                "nombre": p.nombre,
                "correo": p.correo,
                "aliases": list(p.aliases or []),
                "role": p.role,
                "source": p.source,
                "status": p.status,
                "enrollment_status": p.enrollment_status,
                "start_session": p.start_session,
                "end_session": p.end_session,
            }
            for p in self.participants
        ]

    def _by_email(self, key: str) -> list[Participant]:
        return [p for p in self.participants if email_key(p.correo) == key]

    def _by_alias(self, key: str, *, loose: bool) -> list[Participant]:
        key_function = loose_name_key if loose else strict_name_key
        return [p for p in self.participants if key in {key_function(a) for a in p.aliases or []}]

    def _by_name(self, key: str, *, loose: bool) -> list[Participant]:
        key_function = loose_name_key if loose else strict_name_key
        return [p for p in self.participants if key_function(p.nombre) == key]

    @staticmethod
    def _unique(candidates: list[Participant]) -> ResolutionResult | None:
        if len(candidates) == 1:
            return ResolutionResult(ResolutionStatus.RESOLVED, candidates[0])
        if len(candidates) > 1:
            return ResolutionResult(ResolutionStatus.NEEDS_REVIEW)
        return None


def strict_name_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


def loose_name_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", strict_name_key(value))
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize_name(value: str) -> str:
    """Backward-compatible alias for the strict identity key."""
    return strict_name_key(value)


def email_key(value: str | None) -> str:
    return str(value or "").strip().casefold()


def clearly_identifiable(value: str) -> bool:
    return bool(strict_name_key(value)) and bool(re.search(r"[^\W\d_]", value, re.UNICODE))


def auto_participant_id() -> str:
    return f"auto_{uuid.uuid4().hex}"


def _participant_from_record(record: dict[str, Any], default_source: str) -> Participant:
    name = str(record.get("nombre", "")).strip()
    email = str(record.get("correo", "")).strip()
    if not name:
        raise ValueError("Un participante requiere nombre")
    aliases = record.get("aliases", [])
    if isinstance(aliases, str):
        aliases = [item.strip() for item in aliases.split(",") if item.strip()]
    else:
        aliases = [str(item).strip() for item in aliases if str(item).strip()]
    participant_id = str(record.get("participant_id", "")).strip()
    if not participant_id:
        identity = email_key(email) or strict_name_key(name)
        participant_id = f"{default_source}_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
    enrollment_status = str(record.get("enrollment_status", "active")) or "active"
    start_raw = record.get("start_session", 1)
    end_raw = str(record.get("end_session", "")).strip()
    valid = True
    try:
        start_session = int(float(start_raw or 1))
    except (TypeError, ValueError):
        start_session, valid = 1, False
    try:
        end_session = int(float(end_raw)) if end_raw else None
    except (TypeError, ValueError):
        end_session, valid = None, False
    valid = valid and enrollment_status in {"active", "inactive"} and start_session >= 1
    valid = valid and not (enrollment_status == "active" and end_session is not None)
    valid = valid and not (enrollment_status == "inactive" and end_session is None)
    valid = valid and not (end_session is not None and end_session < start_session)
    return Participant(
        participant_id=participant_id,
        nombre=name,
        correo=email,
        aliases=aliases,
        role=record.get("role", "participant"),
        source=str(record.get("source", default_source)),
        status=str(record.get("status", "unverified" if default_source == "auto" else "imported")),
        enrollment_status=str(record.get("enrollment_status", "active")) or "active",
        start_session=start_session,
        end_session=end_session,
        enrollment_config_valid=valid,
    )
