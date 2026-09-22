from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

from .init import get_google_services_with_docs
from ..adapters.google.content import DriveContentReader
from ..adapters.google.source import select_and_inspect
from ..adapters.parsers import DocxParser, GoogleDocsParser, SbvParser, TxtParser, VttParser
from ..adapters.google.sheets.participants import GoogleSheetsValuesGateway, ParticipantRepository
from ..application.program_runner import ProgramDependencies, ProgramRunner
from ..adapters.filesystem.state import FileStateStore
from ..adapters.google.sheets.ranking import GoogleSheetsRankingGateway, RankingRepository
from ..application.registry import DEFAULT_PROGRAMS_PATH, load_programs
from ..application.known_external import load_known_external
from ..application.modules.resolution import EventIdentityResolver
from ..application.session_processor import SessionProcessor
from ..adapters.google.sheets.session_results import GoogleSheetsSessionResultsGateway, SessionResultsRepository
from ..adapters.google.sheets.follow_up import (ControlRepository, FollowUpRepository,
                                    GoogleSheetsFollowUpGateway)
from ..adapters.google.sheets.control import GoogleSheetsControlRepository
from ..addons.follow_up.addon import IndividualFollowUpAddon

from ..adapters.filesystem.state import DEFAULT_STATE_PATH
from ..adapters.google.sheets.tracking import GoogleSheetsTrackingGateway
from ..application.tracking import TrackingRepository
from ..adapters.google.errors import format_google_error, is_expected_google_error



def _sheet_ids(sheets: Any, spreadsheet_id: str) -> dict[str, int]:
    response = sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        includeGridData=False,
        fields="sheets(properties(title,sheetId))",
    ).execute()
    return {
        item["properties"]["title"]: item["properties"]["sheetId"]
        for item in response.get("sheets", [])
        if "title" in item.get("properties", {})
    }


def build_runner(programs_path: Path = DEFAULT_PROGRAMS_PATH,
                 state_path: Path = DEFAULT_STATE_PATH,
                 *, state_store: Any | None = None,
                 cloud_first: bool = False) -> ProgramRunner:
    programs = load_programs(programs_path)
    if not programs:
        def no_dependencies(program_id: str, program: Any) -> ProgramDependencies:
            raise RuntimeError("No hay dependencias para un registro vacío")
        return ProgramRunner(
            programs=programs,
            source=None,
            dependencies_factory=no_dependencies,
            state_store=state_store or FileStateStore(state_path),
            discovery=select_and_inspect,
        )
    drive, sheets, docs = get_google_services_with_docs()
    dependencies: dict[str, ProgramDependencies] = {}

    def make_dependencies(program_id: str, program: Any) -> ProgramDependencies:
        if program_id not in dependencies:
            ids = _sheet_ids(sheets, program.sheet_id)
            participant_repository = ParticipantRepository(
                GoogleSheetsValuesGateway(sheets, program.sheet_id, "Participantes",
                                          ids.get("Participantes")))
            session_results_repository = SessionResultsRepository(
                GoogleSheetsSessionResultsGateway(
                    sheets, program.sheet_id, ids.get("Sesiones", 0), "Sesiones"))
            ranking_repository = RankingRepository(
                GoogleSheetsRankingGateway(
                    sheets, program.sheet_id, ids.get("Ranking"), "Ranking"))
            control_repository = ControlRepository(GoogleSheetsValuesGateway(
                sheets, program.sheet_id, "Control", ids.get("Control")))
            tracking_repository = None
            follow_up_repository = None
            if not cloud_first:
                tracking_gateway = GoogleSheetsTrackingGateway(sheets, program.sheet_id, "Seguimiento")
                tracking_repository = TrackingRepository(
                    tracking_gateway,
                    participant_repository, session_results_repository)
                follow_up_repository = FollowUpRepository(
                    GoogleSheetsFollowUpGateway(sheets, program.sheet_id),
                    participant_repository, session_results_repository, control_repository)
            resolver_factory: Callable[[Iterable[dict[str, Any]]], Any] | None = None
            if program.participant_mode in {"auto", "import", "official"}:
                from ..core.participants import ParticipantResolver
                base_factory = {
                    "auto": ParticipantResolver.auto,
                    "import": ParticipantResolver.imported,
                    "official": ParticipantResolver.official,
                }[program.participant_mode]
                if program.known_external_path:
                    known_external = load_known_external(program.known_external_path)

                    def make_resolver(records: Any, base_factory=base_factory,
                                      known_external=known_external):
                        return EventIdentityResolver(base_factory(records), known_external)
                    resolver_factory = make_resolver
                else:
                    resolver_factory = base_factory
            processor = SessionProcessor(
                content_loader=DriveContentReader(drive, docs),
                parsers=[GoogleDocsParser(), DocxParser(), VttParser(), SbvParser(), TxtParser()],
                participant_repository=participant_repository,
                session_results_repository=session_results_repository,
                resolver_factory=resolver_factory,
            )
            dependencies[program_id] = ProgramDependencies(
                processor, participant_repository, session_results_repository, ranking_repository,
                tracking_repository,
                () if cloud_first else (IndividualFollowUpAddon(follow_up_repository, program.sessions),),
                GoogleSheetsControlRepository(sheets, program.sheet_id),
                enable_legacy_tracking=not cloud_first,
                enable_legacy_addons=not cloud_first)
        return dependencies[program_id]

    return ProgramRunner(
        programs=programs,
        source=drive,
        dependencies_factory=make_dependencies,
        state_store=state_store or FileStateStore(state_path),
        discovery=select_and_inspect,
    )


def _print_results(results: list[Any]) -> None:
    for result in results:
        print(f"Program: {result.program_name}")
        session_messages: set[str] = set()
        for session in result.session_results:
            print(f"Session {session.session_number}: {session.status.value}")
            for warning in tuple(getattr(session, "warnings", ())):
                message = str(warning)
                session_messages.add(message)
                print(f"  WARNING: {message}")
            for error in tuple(getattr(session, "errors", ())):
                message = str(error)
                session_messages.add(message)
                print(f"  ERROR: {message}")

        ranking_status = "UPDATED" if result.ranking_changed else "NOOP"
        print(f"Ranking: {ranking_status}")
        print(
            "Summary: "
            f"processed={result.sessions_processed}, "
            f"skipped={result.sessions_skipped}, "
            f"incomplete={result.sessions_incomplete}, "
            f"needs_review={result.sessions_needs_review}, "
            f"failed={result.sessions_failed}"
        )

        for warning in tuple(getattr(result, "warnings", ())):
            if str(warning) not in session_messages:
                print(f"WARNING: {warning}")
        for error in tuple(getattr(result, "errors", ())):
            if str(error) not in session_messages:
                print(f"ERROR: {error}")


def _run_failed(results: list[Any]) -> bool:
    return any(result.sessions_failed or tuple(getattr(result, "errors", ())) for result in results)


def _print_completion(results: list[Any]) -> None:
    if _run_failed(results):
        print("RESULT: ERROR — the flow completed with one or more failures.", file=sys.stderr)
        return
    attention = sum(
        int(getattr(result, "sessions_incomplete", 0))
        + int(getattr(result, "sessions_needs_review", 0))
        for result in results
    )
    if attention:
        print(f"RESULT: SUCCESS — the flow completed; {attention} session(s) require attention.")
    else:
        print("RESULT: SUCCESS — the flow completed successfully.")


def main(argv: list[str] | None = None, *, runner_factory=build_runner) -> int:
    parser = argparse.ArgumentParser(description="Run one participation synchronization flow")
    parser.add_argument("--program", help="exact program ID or name")
    parser.add_argument("--programs-path", type=Path, default=DEFAULT_PROGRAMS_PATH)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    args = parser.parse_args(argv)
    try:
        results = runner_factory(args.programs_path, args.state_path).run(args.program)
        if not results:
            print(
                "RESULT: ERROR — no programs are registered; run participacion-init first.",
                file=sys.stderr,
            )
            return 1
        _print_results(results)
        _print_completion(results)
        return 1 if _run_failed(results) else 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"RESULT: ERROR — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if not is_expected_google_error(exc):
            print(
                f"RESULT: ERROR — unexpected {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            raise
        print(f"RESULT: ERROR — {format_google_error(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
