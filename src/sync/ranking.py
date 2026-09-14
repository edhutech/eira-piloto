from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .participants import Participant
from .scoring import ParticipantSessionScore


ELIGIBLE_ROLE = "participant"


@dataclass(frozen=True)
class ProgramRankingEntry:
    participant_id: str
    participant: str
    role: str
    sessions_with_activity: int
    voice_valid_total: int
    chat_valid_total: int
    score_total: Decimal
    rank: int
    ranking_complete: bool
    voice_total: int = 0
    chat_total: int = 0


@dataclass(frozen=True)
class ProgramRanking:
    entries: tuple[ProgramRankingEntry, ...]
    ranking_complete: bool
    excluded_scores: dict[str, Decimal]


def build_program_ranking(
    participants: Iterable[Participant],
    session_results: Iterable[ParticipantSessionScore],
) -> ProgramRanking:
    participant_map = _index_participants(participants)
    accumulators: dict[str, _Accumulator] = {}
    excluded_scores: dict[str, Decimal] = {}
    seen_keys: set[tuple[int, str]] = set()

    for result in session_results:
        participant_id = str(result.participant_id).strip()
        if not participant_id or participant_id not in participant_map:
            raise ValueError(f"Resultado con participant_id inexistente: {participant_id}")
        session_number = int(result.session_number)
        key = (session_number, participant_id)
        if key in seen_keys:
            raise ValueError(f"Resultado duplicado para (session_number, participant_id): {key}")
        seen_keys.add(key)
        score = _decimal_score(result.score)
        participant = participant_map[participant_id]
        if participant.role != ELIGIBLE_ROLE:
            excluded_scores[participant_id] = excluded_scores.get(participant_id, Decimal("0")) + score
            continue
        accumulator = accumulators.setdefault(participant_id, _Accumulator())
        accumulator.add(result, score)

    ordered = sorted(
        (
            _entry(participant_map[participant_id], accumulator)
            for participant_id, accumulator in accumulators.items()
        ),
        key=lambda entry: (-entry.score_total, entry.participant.casefold(), entry.participant_id),
    )
    ranked: list[ProgramRankingEntry] = []
    previous_score: Decimal | None = None
    for position, entry in enumerate(ordered, 1):
        rank = position if previous_score != entry.score_total else ranked[-1].rank
        ranked.append(_with_rank(entry, rank))
        previous_score = entry.score_total
    return ProgramRanking(
        entries=tuple(ranked),
        ranking_complete=all(entry.ranking_complete for entry in ranked),
        excluded_scores=excluded_scores,
    )


def top_n(ranking: ProgramRanking, position: int) -> list[ProgramRankingEntry]:
    if position < 1:
        raise ValueError("Top N requiere una posición positiva")
    return [entry for entry in ranking.entries if entry.rank <= position]


@dataclass
class _Accumulator:
    sessions_with_activity: int = 0
    voice_total: int = 0
    voice_valid_total: int = 0
    chat_total: int = 0
    chat_valid_total: int = 0
    score_total: Decimal = Decimal("0")
    ranking_complete: bool = True

    def add(self, result: ParticipantSessionScore, score: Decimal) -> None:
        self.sessions_with_activity += int(result.voice_total + result.chat_total > 0)
        self.voice_total += result.voice_total
        self.voice_valid_total += result.voice_valid
        self.chat_total += result.chat_total
        self.chat_valid_total += result.chat_valid
        self.score_total += score
        self.ranking_complete = self.ranking_complete and result.scoring_complete


def _index_participants(participants: Iterable[Participant]) -> dict[str, Participant]:
    indexed: dict[str, Participant] = {}
    for participant in participants:
        participant_id = str(participant.participant_id).strip()
        if not participant_id:
            raise ValueError("Participante sin participant_id")
        if participant_id in indexed:
            raise ValueError(f"participant_id duplicado: {participant_id}")
        indexed[participant_id] = participant
    return indexed


def _decimal_score(value: Decimal | str | int | float) -> Decimal:
    try:
        score = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Score inválido: {value}") from exc
    if score < 0:
        raise ValueError(f"Score inválido: {value}")
    return score


def _entry(participant: Participant, accumulator: _Accumulator) -> ProgramRankingEntry:
    return ProgramRankingEntry(
        participant_id=participant.participant_id,
        participant=participant.nombre,
        role=participant.role,
        sessions_with_activity=accumulator.sessions_with_activity,
        voice_valid_total=accumulator.voice_valid_total,
        chat_valid_total=accumulator.chat_valid_total,
        score_total=accumulator.score_total,
        rank=0,
        ranking_complete=accumulator.ranking_complete,
        voice_total=accumulator.voice_total,
        chat_total=accumulator.chat_total,
    )


def _with_rank(entry: ProgramRankingEntry, rank: int) -> ProgramRankingEntry:
    return ProgramRankingEntry(
        participant_id=entry.participant_id,
        participant=entry.participant,
        role=entry.role,
        sessions_with_activity=entry.sessions_with_activity,
        voice_valid_total=entry.voice_valid_total,
        chat_valid_total=entry.chat_valid_total,
        score_total=entry.score_total,
        rank=rank,
        ranking_complete=entry.ranking_complete,
        voice_total=entry.voice_total,
        chat_total=entry.chat_total,
    )
