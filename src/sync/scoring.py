from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from .countability import CountabilityStatus, CountabilityDecision


@dataclass(frozen=True)
class ParticipantSessionScore:
    session_number: int
    participant_id: str
    voice_total: int
    voice_valid: int
    chat_total: int
    chat_valid: int
    ambiguous_total: int
    score: Decimal
    scoring_complete: bool


@dataclass
class _Accumulator:
    voice_total: int = 0
    voice_valid: int = 0
    chat_total: int = 0
    chat_valid: int = 0
    ambiguous_total: int = 0
    half_points: int = 0


def score_events(
    events: Iterable[Any], decisions: Iterable[CountabilityDecision]
) -> list[ParticipantSessionScore]:
    """Aggregate already-resolved HUMAN events and countability decisions."""
    event_list = list(events)
    decision_map = _index_decisions(decisions)
    accumulators: dict[tuple[int, str], _Accumulator] = {}
    seen_event_ids: set[str] = set()

    for event in event_list:
        event_id = str(getattr(event, "event_id", ""))
        if event_id in seen_event_ids:
            raise ValueError(f"Evento duplicado: {event_id}")
        seen_event_ids.add(event_id)
        if getattr(event, "identity_type", "HUMAN") == "SYSTEM":
            continue
        if event_id not in decision_map:
            raise ValueError(f"Falta CountabilityDecision para el evento: {event_id}")

        participant_id = str(getattr(event, "participant_id", "")).strip()
        if not participant_id:
            raise ValueError(f"Evento sin participant_id resuelto: {event_id}")
        session_number = int(getattr(event, "session_number"))
        channel = getattr(event, "channel", None)
        if channel not in {"voice", "chat"}:
            raise ValueError(f"Canal inválido para el evento {event_id}: {channel}")

        accumulator = accumulators.setdefault((session_number, participant_id), _Accumulator())
        status = CountabilityStatus(decision_map[event_id].status)
        if channel == "voice":
            accumulator.voice_total += 1
            if status is CountabilityStatus.COUNT:
                accumulator.voice_valid += 1
                accumulator.half_points += 2
        else:
            accumulator.chat_total += 1
            if status is CountabilityStatus.COUNT:
                accumulator.chat_valid += 1
                accumulator.half_points += 1
        if status is CountabilityStatus.AMBIGUOUS:
            accumulator.ambiguous_total += 1

    return [
        ParticipantSessionScore(
            session_number=session_number,
            participant_id=participant_id,
            voice_total=accumulator.voice_total,
            voice_valid=accumulator.voice_valid,
            chat_total=accumulator.chat_total,
            chat_valid=accumulator.chat_valid,
            ambiguous_total=accumulator.ambiguous_total,
            score=Decimal(accumulator.half_points) / Decimal(2),
            scoring_complete=accumulator.ambiguous_total == 0,
        )
        for (session_number, participant_id), accumulator in sorted(accumulators.items())
    ]


def _index_decisions(
    decisions: Iterable[CountabilityDecision],
) -> dict[str, CountabilityDecision]:
    indexed: dict[str, CountabilityDecision] = {}
    for decision in decisions:
        event_id = str(decision.event_id)
        if event_id in indexed:
            raise ValueError(f"Decisión duplicada: {event_id}")
        indexed[event_id] = decision
    return indexed
