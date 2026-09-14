from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..init_program import get_google_services_with_docs
from .content import DriveContentReader
from .drive_discovery import select_and_inspect
from .parsers import DocxParser, GoogleDocsParser, SbvParser, TxtParser, VttParser
from .participant_repository import GoogleSheetsValuesGateway, ParticipantRepository
from .program_runner import FileStateStore, ProgramDependencies, ProgramRunner
from .ranking_repository import GoogleSheetsRankingGateway, RankingRepository
from .registry import DEFAULT_PROGRAMS_PATH, load_programs
from .session_processor import SessionProcessor
from .session_results_repository import GoogleSheetsSessionResultsGateway, SessionResultsRepository
from .sheet_styling import GoogleSheetStyler
from .state import DEFAULT_STATE_PATH
from .tracking_gateway import GoogleSheetsTrackingGateway
from .tracking_repository import TrackingRepository


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
                 state_path: Path = DEFAULT_STATE_PATH) -> ProgramRunner:
    programs = load_programs(programs_path)
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
            participant_repository.ensure_role_validation()
            tracking_gateway = GoogleSheetsTrackingGateway(sheets, program.sheet_id, "Seguimiento")
            tracking_gateway.ensure_sheet(len(program.sessions))
            GoogleSheetStyler(sheets, program.sheet_id).apply()
            tracking_repository = TrackingRepository(
                tracking_gateway,
                participant_repository, session_results_repository)
            resolver_factory = None
            if program.participant_mode == "import":
                from .participants import ParticipantResolver
                resolver_factory = ParticipantResolver.imported
            processor = SessionProcessor(
                content_loader=DriveContentReader(drive, docs),
                parsers=[GoogleDocsParser(), DocxParser(), VttParser(), SbvParser(), TxtParser()],
                participant_repository=participant_repository,
                session_results_repository=session_results_repository,
                resolver_factory=resolver_factory,
            )
            dependencies[program_id] = ProgramDependencies(
                processor, participant_repository, session_results_repository, ranking_repository,
                tracking_repository)
        return dependencies[program_id]

    return ProgramRunner(
        programs=programs,
        drive=drive,
        dependencies_factory=make_dependencies,
        state_store=FileStateStore(state_path),
        discovery=select_and_inspect,
    )


def _print_results(results: list[Any]) -> None:
    for result in results:
        print(result.program_name)
        for session in result.session_results:
            print(f"Sesión {session.session_number}   {session.status.value}")
        ranking_status = "UPDATED" if result.ranking_changed else "NOOP"
        print(f"Ranking    {ranking_status}")
        print()
        print(f"Processed: {result.sessions_processed}")
        print(f"Skipped: {result.sessions_skipped}")
        print(f"Incomplete: {result.sessions_incomplete}")
        print(f"Needs review: {result.sessions_needs_review}")
        print(f"Failed: {result.sessions_failed}")


def main(argv: list[str] | None = None, *, runner_factory=build_runner) -> int:
    parser = argparse.ArgumentParser(description="Ejecuta una sincronización one-shot")
    parser.add_argument("--program", help="ID o nombre exacto del programa")
    parser.add_argument("--programs-path", type=Path, default=DEFAULT_PROGRAMS_PATH)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    args = parser.parse_args(argv)
    try:
        results = runner_factory(args.programs_path, args.state_path).run(args.program)
        _print_results(results)
        return 1 if any(result.sessions_failed or result.errors for result in results) else 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
