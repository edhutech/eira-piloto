from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from .drive_discovery import select_and_inspect
from .models import ProgramInspection, ProgramRecord, SessionInspection
from .ranking import ProgramRanking, build_program_ranking
from .session_processor import SessionProcessResult, SessionProcessStatus, SessionProcessor
from .state import load_state, save_state


class StateStore(Protocol):
    def load(self) -> dict[str, Any]: ...
    def save(self, state: dict[str, Any]) -> None: ...


@dataclass
class FileStateStore:
    path: Any

    def load(self) -> dict[str, Any]:
        return load_state(self.path)

    def save(self, state: dict[str, Any]) -> None:
        save_state(self.path, state)


@dataclass
class ProgramDependencies:
    session_processor: SessionProcessor
    participant_repository: Any
    session_results_repository: Any
    ranking_repository: Any


@dataclass(frozen=True)
class ProgramRunResult:
    program_id: str
    root_folder_id: str
    program_name: str
    sessions_total: int
    sessions_processed: int
    sessions_skipped: int
    sessions_incomplete: int
    sessions_needs_review: int
    sessions_failed: int
    participants_created: int
    session_results_changed: int
    ranking_changed: bool
    ranking_entries: int
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    session_results: tuple[SessionProcessResult, ...] = ()


@dataclass(frozen=True)
class _SessionDecision:
    process: bool
    fingerprints: dict[str, str]


@dataclass
class ProgramRunner:
    """Run registered programs once, without owning CLI or daemon concerns."""

    programs: Mapping[str, ProgramRecord]
    drive: Any
    dependencies_factory: Callable[[str, ProgramRecord], ProgramDependencies]
    state_store: StateStore
    discovery: Callable[[Any, Mapping[str, ProgramRecord], dict[str, Any], str | None], list[ProgramInspection]] = select_and_inspect

    def run(self, program_id: str | None = None) -> list[ProgramRunResult]:
        state = self.state_store.load()
        selected = self._select(program_id)
        inspections = self._discover(selected, state)
        return [self._run_program(pid, program, inspection, state)
                for pid, program, inspection in inspections]

    def _select(self, program_id: str | None) -> list[tuple[str, ProgramRecord]]:
        if program_id is None:
            return list(self.programs.items())
        if program_id in self.programs:
            return [(program_id, self.programs[program_id])]
        matches = [(pid, program) for pid, program in self.programs.items()
                   if program.program_name == program_id]
        if len(matches) == 1:
            return matches
        if not matches:
            raise KeyError(f"Programa no registrado: {program_id}")
        raise ValueError(f"Nombre de programa ambiguo: {program_id}")

    def _discover(self, selected: Sequence[tuple[str, ProgramRecord]], state: dict[str, Any]):
        result = []
        for pid, program in selected:
            inspections = self.discovery(self.drive, {pid: program}, state, pid)
            if len(inspections) != 1:
                raise RuntimeError(f"Discovery inválido para programa: {pid}")
            result.append((pid, program, inspections[0]))
        return result

    def _run_program(self, pid: str, program: ProgramRecord,
                     inspection: ProgramInspection, state: dict[str, Any]) -> ProgramRunResult:
        dependencies = self.dependencies_factory(pid, program)
        program_state = state.setdefault("programs", {}).setdefault(pid, {"sessions": {}})
        sessions_state = program_state.setdefault("sessions", {})
        session_results: list[SessionProcessResult] = []
        warnings: list[str] = []
        errors: list[str] = []
        for session_inspection in sorted(inspection.sessions, key=lambda item: item.session.session_number):
            session_id = session_inspection.session.folder_id
            previous = sessions_state.get(session_id, {})
            decision = self._decision(session_inspection, previous)
            if not decision.process:
                session_results.append(SessionProcessResult(
                    session_number=session_inspection.session.session_number,
                    status=SessionProcessStatus.SKIPPED,
                ))
                continue
            sessions_state[session_id] = self._state_record(session_inspection, "PROCESSING", decision.fingerprints)
            self.state_store.save(state)
            # Do not catch programming exceptions: PROCESSING remains durable.
            outcome = dependencies.session_processor.process(session_inspection)
            sessions_state[session_id] = self._state_record(
                session_inspection, outcome.status.value, decision.fingerprints)
            self.state_store.save(state)
            session_results.append(outcome)
            warnings.extend(outcome.warnings)
            errors.extend(outcome.errors)

        ranking_changed = False
        ranking_entries = 0
        try:
            participants = dependencies.participant_repository.load()
            scores = dependencies.session_results_repository.load_scores()
            ranking: ProgramRanking = build_program_ranking(participants, scores)
            write_result = dependencies.ranking_repository.persist(ranking, participants)
            ranking_changed = str(getattr(write_result, "status", "REPLACE")) != "NOOP"
            ranking_entries = len(ranking.entries)
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

        counts = {status: sum(item.status is status for item in session_results)
                  for status in SessionProcessStatus}
        return ProgramRunResult(
            program_id=pid, root_folder_id=program.folder_id, program_name=program.program_name,
            sessions_total=len(session_results), sessions_processed=counts[SessionProcessStatus.PROCESSED],
            sessions_skipped=counts[SessionProcessStatus.SKIPPED],
            sessions_incomplete=counts[SessionProcessStatus.INCOMPLETE],
            sessions_needs_review=counts[SessionProcessStatus.NEEDS_REVIEW],
            sessions_failed=counts[SessionProcessStatus.FAILED],
            participants_created=sum(item.participants_created for item in session_results),
            session_results_changed=sum(item.changed for item in session_results),
            ranking_changed=ranking_changed, ranking_entries=ranking_entries,
            warnings=tuple(warnings), errors=tuple(errors),
            session_results=tuple(session_results),
        )

    @staticmethod
    def _decision(inspection: SessionInspection, previous: Mapping[str, Any]) -> _SessionDecision:
        fingerprints = {change.file.file_id: change.fingerprint for change in inspection.changes}
        status = previous.get("status")
        if status in {"PROCESSING", "PENDING", "FAILED", "NEEDS_REVIEW"}:
            return _SessionDecision(True, fingerprints)
        if status in {"PROCESSED", "INCOMPLETE"} and previous.get("files", {}) == fingerprints:
            return _SessionDecision(False, fingerprints)
        return _SessionDecision(True, fingerprints)

    @staticmethod
    def _state_record(inspection: SessionInspection, status: str,
                      fingerprints: dict[str, str]) -> dict[str, Any]:
        return {
            "session_number": inspection.session.session_number,
            "session_name": inspection.session.session_name,
            "folder_id": inspection.session.folder_id,
            "status": status,
            "files": dict(fingerprints),
        }
