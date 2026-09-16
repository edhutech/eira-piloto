import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from participacion.cli import main
from participacion.cli.main import build_runner
from participacion.core.models import ProgramRecord, ProviderRef
from participacion.core.participants import ParticipantResolver


class FakeRunner:
    def __init__(self, results):
        self.results = results
        self.calls = []
    def run(self, program_id=None):
        self.calls.append(program_id)
        return self.results


class CliTests(unittest.TestCase):
    def test_cli_processes_all_programs_and_returns_zero(self):
        runner = FakeRunner([SimpleNamespace(
            program_name="Programa", session_results=[], ranking_changed=False,
            sessions_processed=1, sessions_skipped=0, sessions_incomplete=0,
            sessions_needs_review=0, sessions_failed=0, errors=(), events=(),
        )])
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([], runner_factory=lambda programs, state: runner)
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, [None])
        self.assertIn("Processed: 1", output.getvalue())

    def test_cli_optional_program_selection_is_forwarded(self):
        runner = FakeRunner([])
        with redirect_stdout(io.StringIO()):
            code = main(["--program", "Programa"], runner_factory=lambda programs, state: runner)
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, ["Programa"])

    def test_cli_returns_one_when_runner_reports_failed_session_or_error(self):
        for failed, errors in ((1, ()), (0, ("ranking failure",))):
            runner = FakeRunner([SimpleNamespace(
                program_name="Programa", session_results=[], ranking_changed=False,
                sessions_processed=0, sessions_skipped=0, sessions_incomplete=0,
                sessions_needs_review=0, sessions_failed=failed, errors=errors, events=(),
            )])
            with redirect_stdout(io.StringIO()):
                code = main([], runner_factory=lambda programs, state, r=runner: r)
            self.assertEqual(code, 1)

    def test_cli_operational_error_returns_one(self):
        def failing_factory(programs, state):
            raise RuntimeError("service unavailable")
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([], runner_factory=failing_factory)
        self.assertEqual(code, 1)
        self.assertIn("ERROR", output.getvalue())

    def test_real_runner_dependency_factory_resolves_core_participant_resolver(self):
        program = ProgramRecord(
            "program", "Synthetic program", 1, "auto",
            ProviderRef("fake", "folder"), ProviderRef("fake", "sheet"), (),
        )
        services = (object(), object(), object())
        repositories = [SimpleNamespace(ensure_role_validation=lambda: None)]
        processor = MagicMock()
        with patch("participacion.cli.main.load_programs", return_value={"program": program}), \
             patch("participacion.cli.main.get_google_services_with_docs", return_value=services), \
             patch("participacion.cli.main._sheet_ids", return_value={}), \
             patch("participacion.cli.main.ParticipantRepository", return_value=repositories[0]), \
             patch("participacion.cli.main.SessionResultsRepository"), \
             patch("participacion.cli.main.RankingRepository"), \
             patch("participacion.cli.main.GoogleSheetsTrackingGateway"), \
             patch("participacion.cli.main.TrackingRepository"), \
             patch("participacion.cli.main.ControlRepository"), \
             patch("participacion.cli.main.FollowUpRepository"), \
             patch("participacion.cli.main.SessionProcessor", return_value=processor) as processor_constructor, \
             patch("participacion.cli.main.GoogleSheetStyler"):
            runner = build_runner()
            dependencies = runner.dependencies_factory("program", program)
        self.assertIs(processor_constructor.call_args.kwargs["resolver_factory"].__func__, ParticipantResolver.auto.__func__)


if __name__ == "__main__":
    unittest.main()
