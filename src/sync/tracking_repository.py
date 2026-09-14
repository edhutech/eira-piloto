from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .models import SessionRecord
from .participants import Participant
from .scoring import ParticipantSessionScore

TRACKING_TITLE = "Seguimiento"
TRACKING_FIXED_HEADERS = ("participant_id", "Participante", "Email")


@dataclass(frozen=True)
class TrackingParticipant:
    participant_id: str
    participant: str
    email: str


@dataclass(frozen=True)
class TrackingSession:
    session_number: int
    session_name: str
    status: str
    attendance_total: int | None
    participated: int | None
    participation_pct: Decimal | None
    metrics: Mapping[str, tuple[Decimal, Decimal, Decimal] | None]


@dataclass(frozen=True)
class TrackingView:
    participants: tuple[TrackingParticipant, ...]
    sessions: tuple[TrackingSession, ...]


@dataclass(frozen=True)
class TrackingWriteResult:
    status: str
    rows_written: int


class TrackingGateway(Protocol):
    def ensure_sheet(self, session_count: int) -> tuple[bool, int | None]: ...
    def read_values(self) -> list[list[Any]]: ...
    def persist(self, values: list[list[Any]], manual_cells: set[tuple[int, int]],
                structural_change: bool, worksheet_id: int | None) -> TrackingWriteResult: ...


def build_tracking_view(
    participants: Iterable[Participant],
    sessions: Iterable[SessionRecord],
    session_statuses: Mapping[int, str],
    session_results: Iterable[ParticipantSessionScore],
    attendance: Mapping[int, int | None],
) -> TrackingView:
    eligible = tuple(sorted(
        (TrackingParticipant(p.participant_id, p.nombre, p.correo)
         for p in participants if p.role == "participant"),
        key=lambda p: (p.participant.casefold(), p.participant_id),
    ))
    participant_ids = {p.participant_id for p in eligible}
    by_key = {(r.session_number, r.participant_id): r for r in session_results
              if r.participant_id in participant_ids}
    tracking_sessions: list[TrackingSession] = []
    for session in sorted(sessions, key=lambda item: item.session_number):
        status = session_statuses.get(session.session_number, "PENDING")
        confirmed = status == "PROCESSED"
        rows = {p.participant_id: by_key.get((session.session_number, p.participant_id)) for p in eligible}
        active = [r for r in rows.values() if r is not None and (r.voice_valid + r.chat_valid) > 0]
        total = attendance.get(session.session_number)
        pct = None if total in (None, 0) else Decimal(len(active) * 100) / Decimal(total)
        metrics: dict[str, tuple[Decimal, Decimal, Decimal] | None] = {}
        for participant in eligible:
            result = rows[participant.participant_id]
            if not confirmed:
                metrics[participant.participant_id] = None
                continue
            chat = Decimal(result.chat_valid) / Decimal(2) if result else Decimal("0")
            oral = Decimal(result.voice_valid) if result else Decimal("0")
            metrics[participant.participant_id] = (chat, oral, chat + oral)
        tracking_sessions.append(TrackingSession(
            session.session_number, session.session_name, status, total,
            len(active) if confirmed else None, pct if confirmed else None, metrics,
        ))
    return TrackingView(eligible, tuple(tracking_sessions))


def tracking_values(view: TrackingView) -> tuple[list[list[Any]], set[tuple[int, int]]]:
    block_count = len(view.sessions)
    width = 3 + block_count * 3
    row1 = ["Seguimiento", "", ""]
    row2 = ["Participante", "Email", ""]
    row3 = ["", "", ""]
    row4 = ["", "", ""]
    row5 = ["", "", ""]
    for index, session in enumerate(view.sessions):
        label = f"Clase {session.session_number} / {session.session_name}"
        row1.extend([label, "", ""])
        row2.extend(["Asistentes", session.attendance_total if session.attendance_total is not None else "", ""])
        row3.extend(["Participaron", session.participated if session.participated is not None else "", ""])
        participated_column = _column_name(5 + index * 3)
        attendance_column = participated_column
        row4.extend(["% participación", f'=IF(OR({attendance_column}2="",{attendance_column}2=0,{participated_column}3=""),"",{participated_column}3/{attendance_column}2)', ""])
        row5.extend(["Chat", "Oral", "Total"])
    rows = [row1, row2, row3, row4, row5]
    for participant in view.participants:
        row = [participant.participant_id, participant.participant, participant.email]
        for session in view.sessions:
            metrics = session.metrics[participant.participant_id]
            if metrics is None:
                row.extend(["", "", ""])
            else:
                row.extend(list(metrics))
        rows.append(row)
    rows = [row + [""] * (width - len(row)) for row in rows]
    manual_cells = {(1, 4 + 3 * index) for index, _ in enumerate(view.sessions)}
    return rows, manual_cells


def _column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


class TrackingRepository:
    """Build and persist the human-facing derived Seguimiento view."""

    def __init__(self, gateway: TrackingGateway, participant_repository: Any,
                 session_results_repository: Any):
        self.gateway = gateway
        self.participant_repository = participant_repository
        self.session_results_repository = session_results_repository

    def refresh(self, sessions: Sequence[SessionRecord], session_statuses: Mapping[int, str]) -> TrackingWriteResult:
        created, worksheet_id = self.gateway.ensure_sheet(len(sessions))
        existing = self.gateway.read_values()
        attendance = self._attendance(existing, sessions)
        view = build_tracking_view(
            self.participant_repository.load(), sessions, session_statuses,
            self.session_results_repository.load_scores(), attendance,
        )
        values, manual_cells = tracking_values(view)
        structural = created or self._structure_differs(existing, values)
        return self.gateway.persist(values, manual_cells, structural, worksheet_id)

    @staticmethod
    def _attendance(values: Sequence[Sequence[Any]], sessions: Sequence[SessionRecord]) -> dict[int, int | None]:
        result: dict[int, int | None] = {}
        if not values:
            return result
        labels = values[0]
        row = values[1] if len(values) > 1 else []
        for index, session in enumerate(sessions):
            start = 3 + index * 3
            if start >= len(labels) or not str(labels[start]).startswith(f"Clase {session.session_number} /"):
                start = next((i for i, value in enumerate(labels)
                              if str(value).startswith(f"Clase {session.session_number} /")), -1)
            value = row[start + 1] if start >= 0 and start + 1 < len(row) else ""
            if str(value).strip() == "":
                result[session.session_number] = None
            else:
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    raise ValueError(f"Asistentes inválido para sesión {session.session_number}: {value}")
                if parsed < 0:
                    raise ValueError(f"Asistentes inválido para sesión {session.session_number}: {value}")
                result[session.session_number] = parsed
        return result

    @staticmethod
    def _structure_differs(existing: Sequence[Sequence[Any]], desired: Sequence[Sequence[Any]]) -> bool:
        if not existing:
            return True
        return list(existing[0]) != list(desired[0]) or len(existing) != len(desired)
