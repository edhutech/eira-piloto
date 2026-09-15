from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable, Mapping, Sequence

from .models import SessionRecord
from .participants import Participant
from .scoring import ParticipantSessionScore

FOLLOW_UP_RULESET_VERSION = 1


class FollowUpLevel(str, Enum):
    NORMAL = "Normal"
    OBSERVE = "Observar"
    CRITICAL = "Crítico"


class FollowUpReasonCode(str, Enum):
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    NEVER_PARTICIPATED = "NEVER_PARTICIPATED"
    NO_RECENT_PARTICIPATION = "NO_RECENT_PARTICIPATION"
    LOW_HISTORY_NO_RECENT = "LOW_HISTORY_NO_RECENT"
    LOW_HISTORY_LOW_RECENT = "LOW_HISTORY_LOW_RECENT"
    LOW_RECENT = "LOW_RECENT"
    IRREGULAR = "IRREGULAR"
    RECENT_RECOVERY = "RECENT_RECOVERY"
    SUSTAINED = "SUSTAINED"
    INACTIVE = "INACTIVE"
    INVALID_ENROLLMENT_CONFIG = "INVALID_ENROLLMENT_CONFIG"


REASON_TEXT = {
    FollowUpReasonCode.INSUFFICIENT_HISTORY: "Historial insuficiente",
    FollowUpReasonCode.NEVER_PARTICIPATED: "Sin participación registrada",
    FollowUpReasonCode.NO_RECENT_PARTICIPATION: "Dejó de participar en las últimas 4 sesiones",
    FollowUpReasonCode.LOW_HISTORY_NO_RECENT: "Participación histórica muy baja y sin actividad reciente",
    FollowUpReasonCode.LOW_HISTORY_LOW_RECENT: "Participación histórica muy baja y participación reciente limitada",
    FollowUpReasonCode.LOW_RECENT: "Baja participación reciente",
    FollowUpReasonCode.IRREGULAR: "Participación irregular",
    FollowUpReasonCode.RECENT_RECOVERY: "Mejora reciente con histórico de participación bajo",
    FollowUpReasonCode.SUSTAINED: "Participación sostenida",
    FollowUpReasonCode.INACTIVE: "Estudiante inactivo",
    FollowUpReasonCode.INVALID_ENROLLMENT_CONFIG: "Configuración de matrícula incompleta",
}


@dataclass(frozen=True)
class FollowUpEntry:
    participant_id: str
    participant: str
    email: str
    enrollment_status: str
    eligible_sessions: int
    participation_sessions: int
    historical_frequency: Decimal | None
    recent_window: str
    last_participation_session: int | None
    score_total: Decimal
    follow_up_level: FollowUpLevel | None
    follow_up_since_session: int | None
    reason_code: FollowUpReasonCode
    reason: str
    ruleset_version: int = FOLLOW_UP_RULESET_VERSION


@dataclass(frozen=True)
class ProgramFollowUp:
    entries: tuple[FollowUpEntry, ...]
    normal_count: int
    observe_count: int
    critical_count: int
    insufficient_history_count: int
    ruleset_version: int = FOLLOW_UP_RULESET_VERSION


def enrollment_issue(participant: Participant) -> bool:
    if not participant.enrollment_config_valid:
        return True
    if participant.enrollment_status not in {"active", "inactive"}:
        return True
    if participant.start_session < 1:
        return True
    if participant.enrollment_status == "active":
        return participant.end_session is not None
    return participant.end_session is None or participant.end_session < participant.start_session


def participant_is_eligible(participant: Participant, session_number: int) -> bool:
    if enrollment_issue(participant) or participant.role != "participant":
        return False
    if session_number < participant.start_session:
        return False
    return participant.end_session is None or session_number <= participant.end_session


def build_program_follow_up(
    participants: Iterable[Participant],
    sessions: Sequence[SessionRecord],
    session_statuses: Mapping[int, str],
    tracking_eligible: Mapping[int, bool],
    session_results: Iterable[ParticipantSessionScore],
    *,
    official: bool = True,
) -> ProgramFollowUp:
    if not official:
        return ProgramFollowUp((), 0, 0, 0, 0)
    people = sorted((p for p in participants if p.role == "participant"),
                    key=lambda p: (p.nombre.casefold(), p.participant_id))
    by_key: dict[tuple[int, str], ParticipantSessionScore] = {}
    all_scores: dict[str, Decimal] = {}
    for score in session_results:
        key = (score.session_number, score.participant_id)
        if key in by_key:
            raise ValueError(f"Resultado duplicado: {key}")
        by_key[key] = score
        all_scores[score.participant_id] = all_scores.get(score.participant_id, Decimal("0")) + score.score
    processed_sessions = [s for s in sorted(sessions, key=lambda item: item.session_number)
                          if session_statuses.get(s.session_number) == "PROCESSED"
                          and tracking_eligible.get(s.session_number, True)]
    entries = tuple(_entry(p, processed_sessions, by_key, all_scores.get(p.participant_id, Decimal("0")))
                    for p in people)
    active = [e for e in entries if e.enrollment_status == "active"]
    return ProgramFollowUp(
        entries=tuple(sorted(entries, key=_display_order_key)),
        normal_count=sum(e.follow_up_level is FollowUpLevel.NORMAL for e in active),
        observe_count=sum(e.follow_up_level is FollowUpLevel.OBSERVE for e in active),
        critical_count=sum(e.follow_up_level is FollowUpLevel.CRITICAL for e in active),
        insufficient_history_count=sum(e.reason_code is FollowUpReasonCode.INSUFFICIENT_HISTORY for e in active),
    )


def _entry(participant: Participant, processed_sessions: Sequence[SessionRecord],
           scores: Mapping[tuple[int, str], ParticipantSessionScore], score_total: Decimal) -> FollowUpEntry:
    if enrollment_issue(participant):
        return FollowUpEntry(participant.participant_id, participant.nombre, participant.correo,
            participant.enrollment_status, 0, 0, None, "", None, score_total, None, None,
            FollowUpReasonCode.INVALID_ENROLLMENT_CONFIG, REASON_TEXT[FollowUpReasonCode.INVALID_ENROLLMENT_CONFIG])
    eligible = [s for s in processed_sessions if participant_is_eligible(participant, s.session_number)]
    participation = [bool((scores.get((s.session_number, participant.participant_id)) and
                            (scores[(s.session_number, participant.participant_id)].voice_valid +
                             scores[(s.session_number, participant.participant_id)].chat_valid) > 0))
                     for s in eligible]
    participated_count = sum(participation)
    last = next((s.session_number for s, active in reversed(list(zip(eligible, participation))) if active), None)
    recent = participation[-4:]
    recent_window = " ".join("●" if active else "○" for active in recent)
    frequency = (Decimal(participated_count) / Decimal(len(eligible))
                 if eligible else None)
    if participant.enrollment_status == "inactive":
        return FollowUpEntry(participant.participant_id, participant.nombre, participant.correo,
            participant.enrollment_status, len(eligible), participated_count, frequency, recent_window,
            last, score_total, None, None, FollowUpReasonCode.INACTIVE, REASON_TEXT[FollowUpReasonCode.INACTIVE])
    if len(eligible) < 4:
        return FollowUpEntry(participant.participant_id, participant.nombre, participant.correo,
            participant.enrollment_status, len(eligible), participated_count, frequency, recent_window,
            last, score_total, None, None, FollowUpReasonCode.INSUFFICIENT_HISTORY,
            REASON_TEXT[FollowUpReasonCode.INSUFFICIENT_HISTORY])
    frequency_value = frequency or Decimal("0")
    level = _classify(frequency_value, sum(recent))
    since = _since(eligible, participation)
    reason_code = _reason(frequency_value, sum(recent), participated_count)
    return FollowUpEntry(participant.participant_id, participant.nombre, participant.correo,
        participant.enrollment_status, len(eligible), participated_count, frequency, recent_window,
        last, score_total, level, since, reason_code, REASON_TEXT[reason_code])


def _classify(frequency: Decimal, recent_count: int) -> FollowUpLevel:
    if recent_count == 0:
        return FollowUpLevel.CRITICAL
    if recent_count == 1:
        return FollowUpLevel.CRITICAL if frequency <= Decimal("0.20") else FollowUpLevel.OBSERVE
    if recent_count == 2:
        return FollowUpLevel.OBSERVE if frequency < Decimal("0.50") else FollowUpLevel.NORMAL
    return FollowUpLevel.OBSERVE if frequency <= Decimal("0.20") else FollowUpLevel.NORMAL


def _reason(frequency: Decimal, recent_count: int, total: int) -> FollowUpReasonCode:
    if total == 0:
        return FollowUpReasonCode.NEVER_PARTICIPATED
    if recent_count == 0:
        if frequency <= Decimal("0.20"):
            return FollowUpReasonCode.LOW_HISTORY_NO_RECENT
        return FollowUpReasonCode.NO_RECENT_PARTICIPATION
    if frequency <= Decimal("0.20") and recent_count == 1:
        return FollowUpReasonCode.LOW_HISTORY_LOW_RECENT
    if frequency <= Decimal("0.20") and recent_count >= 3:
        return FollowUpReasonCode.RECENT_RECOVERY
    if recent_count == 1:
        return FollowUpReasonCode.LOW_RECENT
    if recent_count == 2:
        return FollowUpReasonCode.IRREGULAR
    return FollowUpReasonCode.SUSTAINED


def _since(sessions: Sequence[SessionRecord], participation: Sequence[bool]) -> int | None:
    current: FollowUpLevel | None = None
    since: int | None = None
    for index in range(3, len(participation)):
        level = _classify(Decimal(sum(participation[:index + 1])) / Decimal(index + 1),
                          sum(participation[max(0, index - 3):index + 1]))
        if level is not current:
            current, since = level, sessions[index].session_number
    return since


def _display_order_key(entry: FollowUpEntry) -> tuple[int, str, str]:
    order = {FollowUpLevel.CRITICAL: 0, FollowUpLevel.OBSERVE: 1, FollowUpLevel.NORMAL: 2, None: 3}
    inactive_offset = 4 if entry.enrollment_status == "inactive" else 0
    return (inactive_offset or order[entry.follow_up_level], entry.participant.casefold(), entry.participant_id)
