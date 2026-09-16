from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

from .init import get_google_services_with_docs
from ..adapters.google.content import DriveContentReader
from ..adapters.google.source import select_and_inspect
from ..adapters.parsers import DocxParser, GoogleDocsParser, SbvParser, TxtParser, VttParser
from ..adapters.google.sheets.participants import GoogleSheetsValuesGateway, ParticipantRepository
from ..application.program_runner import ProgramDependencies, ProgramRunner
from ..adapters.filesystem.state import FileStateStore
from ..adapters.google.sheets.ranking import GoogleSheetsRankingGateway, RankingRepository
from ..application.registry import DEFAULT_PROGRAMS_PATH, load_programs
from ..application.session_processor import SessionProcessor
from ..adapters.google.sheets.session_results import GoogleSheetsSessionResultsGateway, SessionResultsRepository
from ..adapters.google.sheets.follow_up import (ControlRepository, FollowUpRepository,
                                    GoogleSheetsFollowUpGateway)
from ..application.events import ApplicationEvent
from ..application.notifications import notify_events
from ..addons.follow_up.addon import IndividualFollowUpAddon
from ..adapters.notifications.none import NoneNotifier
from ..adapters.notifications.notify_send import NotifySendNotifier
from ..adapters.notifications.stdout import StdoutNotifier

from ..adapters.filesystem.state import DEFAULT_STATE_PATH
from ..adapters.google.sheets.tracking import GoogleSheetsTrackingGateway
from ..application.tracking import TrackingRepository
from ..adapters.google.errors import format_google_error, is_expected_google_error

logger = logging.getLogger(__name__)


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
    if not programs:
        def no_dependencies(program_id: str, program: Any) -> ProgramDependencies:
            raise RuntimeError("No hay dependencias para un registro vacío")
        return ProgramRunner(
            programs=programs,
            source=None,
            dependencies_factory=no_dependencies,
            state_store=FileStateStore(state_path),
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
            tracking_gateway = GoogleSheetsTrackingGateway(sheets, program.sheet_id, "Seguimiento")
            tracking_repository = TrackingRepository(
                tracking_gateway,
                participant_repository, session_results_repository)
            control_repository = ControlRepository(GoogleSheetsValuesGateway(
                sheets, program.sheet_id, "Control", ids.get("Control")))
            follow_up_repository = FollowUpRepository(
                GoogleSheetsFollowUpGateway(sheets, program.sheet_id),
                participant_repository, session_results_repository, control_repository)
            resolver_factory = None
            if program.participant_mode in {"auto", "import", "official"}:
                from ..core.participants import ParticipantResolver
                resolver_factory = {
                    "auto": ParticipantResolver.auto,
                    "import": ParticipantResolver.imported,
                    "official": ParticipantResolver.official,
                }[program.participant_mode]
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
                (IndividualFollowUpAddon(follow_up_repository, program.sessions),))
        return dependencies[program_id]

    return ProgramRunner(
        programs=programs,
        source=drive,
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


def _notifier(name: str):
    return {"none": NoneNotifier, "stdout": StdoutNotifier,
            "notify-send": NotifySendNotifier}[name]()


def main(argv: list[str] | None = None, *, runner_factory=build_runner,
         notifier_factory=None) -> int:
    parser = argparse.ArgumentParser(description="Ejecuta una sincronización one-shot")
    parser.add_argument("--program", help="ID o nombre exacto del programa")
    parser.add_argument("--programs-path", type=Path, default=DEFAULT_PROGRAMS_PATH)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--no-notify", action="store_true", help="deshabilita notificaciones de escritorio")
    parser.add_argument("--notifier", choices=("none", "stdout", "notify-send"),
                        default=os.environ.get("PARTICIPACION_NOTIFIER", "none"))
    args = parser.parse_args(argv)
    try:
        results = runner_factory(args.programs_path, args.state_path).run(args.program)
        if not results:
            print("ERROR: no hay programas registrados; ejecuta participacion-init", file=sys.stderr)
            if not args.no_notify:
                try:
                    notifier = notifier_factory() if notifier_factory else _notifier(args.notifier)
                    notify_events(notifier, (ApplicationEvent(
                        "runtime.failed", "", "participacion-sync", {},
                    ),))
                except Exception as notify_exc:
                    logger.warning("notification failed: %s", notify_exc)
            return 1
        _print_results(results)
        if not args.no_notify:
            try:
                notifier = notifier_factory() if notifier_factory else _notifier(args.notifier)
                for result in results:
                    notify_events(notifier, result.events)
            except Exception as exc:
                logger.warning("notification failed: %s", exc)
        return 1 if any(result.sessions_failed or result.errors for result in results) else 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        if not args.no_notify:
            try:
                notifier = notifier_factory() if notifier_factory else _notifier(args.notifier)
                notify_events(notifier, (ApplicationEvent(
                    "runtime.failed", "", args.program or "participacion-sync", {},
                ),))
            except Exception as notify_exc:
                logger.warning("notification failed: %s", notify_exc)
        return 1
    except Exception as exc:
        if not is_expected_google_error(exc):
            raise
        print(f"ERROR: {format_google_error(exc)}", file=sys.stderr)
        if not args.no_notify:
            try:
                notifier = notifier_factory() if notifier_factory else _notifier(args.notifier)
                notify_events(notifier, (ApplicationEvent(
                    "runtime.failed", "", args.program or "participacion-sync", {},
                ),))
            except Exception as notify_exc:
                logger.warning("notification failed: %s", notify_exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
