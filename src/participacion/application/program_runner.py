from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from ..core.models import ProgramInspection, ProgramRecord, SessionInspection
from ..core.ranking import ProgramRanking, build_program_ranking
from .events import AddonResult, ApplicationEvent, ProgramAddon, ProgramAddonContext
from .session_processor import SessionProcessResult, SessionProcessStatus, SessionProcessor
from .ports.contracts import StateStore

PROCESSING_PIPELINE_VERSION = 2


@dataclass
class ProgramDependencies:
    session_processor: SessionProcessor
    participant_repository: Any
    session_results_repository: Any
    ranking_repository: Any
    tracking_repository: Any | None = None
    addons: Sequence[ProgramAddon] = ()
    control_repository: Any | None = None
    enable_legacy_tracking: bool = True
    enable_legacy_addons: bool = True


@dataclass(frozen=True)
class ProgramRunResult:
    program_id: str
    root_source_ref: str
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
    tracking_changed: bool = False
    addon_results: tuple[AddonResult, ...] = ()
    events: tuple[ApplicationEvent, ...] = ()
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
    source: Any
    dependencies_factory: Callable[[str, ProgramRecord], ProgramDependencies]
    state_store: StateStore
    discovery: Callable[..., list[ProgramInspection]]

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
            inspections = self.discovery(self.source, {pid: program}, state, pid)
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
            session_id = session_inspection.session.state_key
            if (session_inspection.session.planned and
                    not session_inspection.session.evidence_sources and
                    not session_inspection.session.source_ref):
                session_results.append(SessionProcessResult(
                    session_number=session_inspection.session.session_number,
                    status=SessionProcessStatus.SKIPPED,
                ))
                continue
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

        session_statuses = {
            session.session_number: sessions_state[session.state_key]["status"]
            for session in program.sessions if session.state_key in sessions_state
        }
        if dependencies.control_repository is not None:
            try:
                processed_at = {
                    item.session_number: datetime.now(timezone.utc).isoformat(timespec="seconds")
                    for item in session_results if item.status is not SessionProcessStatus.SKIPPED
                }
                dependencies.control_repository.reconcile(
                    inspection.sessions, session_statuses, processed_at)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

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

        tracking_changed = False
        if (dependencies.enable_legacy_tracking and dependencies.tracking_repository is not None and
                any(item.status is not SessionProcessStatus.SKIPPED for item in session_results)):
            try:
                tracking_result = dependencies.tracking_repository.refresh(
                    program.sessions,
                    {session.session_number: sessions_state[session.state_key]["status"]
                     for session in program.sessions if session.state_key in sessions_state},
                )
                tracking_changed = str(getattr(tracking_result, "status", "REPLACE")) != "NOOP"
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

        addon_results: list[AddonResult] = []
        events: list[ApplicationEvent] = []
        addons = dependencies.addons if dependencies.enable_legacy_addons else ()
        for addon in addons:
            try:
                addon_result = addon.run(ProgramAddonContext(program, session_statuses))
            except (OSError, RuntimeError, ValueError) as exc:
                addon_result = AddonResult(
                    getattr(addon, "addon_id", type(addon).__name__),
                    errors=(f"{type(exc).__name__}: {exc}",),
                )
            addon_results.append(addon_result)
            events.extend(addon_result.events)
            warnings.extend(addon_result.warnings)
            errors.extend(addon_result.errors)

        counts = {status: sum(item.status is status for item in session_results)
                  for status in SessionProcessStatus}
        if counts[SessionProcessStatus.FAILED] or errors:
            events.append(ApplicationEvent("program.failed", pid, program.program_name, {
                "sessions_failed": counts[SessionProcessStatus.FAILED],
                "error_count": len(errors),
            }))
        elif counts[SessionProcessStatus.INCOMPLETE] or counts[SessionProcessStatus.NEEDS_REVIEW]:
            events.append(ApplicationEvent("program.requires_attention", pid, program.program_name, {
                "incomplete": counts[SessionProcessStatus.INCOMPLETE],
                "needs_review": counts[SessionProcessStatus.NEEDS_REVIEW],
            }))
        elif (sum(item.changed for item in session_results) > 0 or
              ranking_changed or tracking_changed):
            events.append(ApplicationEvent("program.updated", pid, program.program_name, {
                "sessions_processed": counts[SessionProcessStatus.PROCESSED],
                "participants_created": sum(item.participants_created for item in session_results),
                "ranking_changed": ranking_changed,
                "tracking_changed": tracking_changed,
            }))
        return ProgramRunResult(
            program_id=pid, root_source_ref=program.source.ref, program_name=program.program_name,
            sessions_total=len(session_results), sessions_processed=counts[SessionProcessStatus.PROCESSED],
            sessions_skipped=counts[SessionProcessStatus.SKIPPED],
            sessions_incomplete=counts[SessionProcessStatus.INCOMPLETE],
            sessions_needs_review=counts[SessionProcessStatus.NEEDS_REVIEW],
            sessions_failed=counts[SessionProcessStatus.FAILED],
            participants_created=sum(item.participants_created for item in session_results),
            session_results_changed=sum(item.changed for item in session_results),
            ranking_changed=ranking_changed, ranking_entries=ranking_entries,
            tracking_changed=tracking_changed,
            addon_results=tuple(addon_results), events=tuple(events),
            warnings=tuple(warnings), errors=tuple(errors),
            session_results=tuple(session_results),
        )

    @staticmethod
    def _decision(inspection: SessionInspection, previous: Mapping[str, Any]) -> _SessionDecision:
        fingerprints = {change.file.artifact_id: change.fingerprint for change in inspection.changes}
        status = previous.get("status")
        if status in {"PROCESSING", "PENDING", "FAILED", "NEEDS_REVIEW"}:
            return _SessionDecision(True, fingerprints)
        if (status in {"PROCESSED", "INCOMPLETE"}
                and previous.get("processing_version") == PROCESSING_PIPELINE_VERSION
                and previous.get("files", {}) == fingerprints):
            return _SessionDecision(False, fingerprints)
        return _SessionDecision(True, fingerprints)

    @staticmethod
    def _state_record(inspection: SessionInspection, status: str,
                      fingerprints: dict[str, str]) -> dict[str, Any]:
        return {
            "session_number": inspection.session.session_number,
            "session_name": inspection.session.session_name,
            "folder_id": inspection.session.state_key,
            "status": status,
            "processing_version": PROCESSING_PIPELINE_VERSION,
            "files": dict(fingerprints),
        }
