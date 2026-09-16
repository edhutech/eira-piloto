import io
import importlib
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from pathlib import Path

from participacion.cli import main
from participacion.cli.main import build_runner
from participacion.core.models import ProgramRecord, ProviderRef
from participacion.core.participants import ParticipantResolver
from google.auth.exceptions import RefreshError

CLI_MAIN_MODULE = importlib.import_module("participacion.cli.main")


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
        runner = FakeRunner([SimpleNamespace(
            program_name="Programa", session_results=[], ranking_changed=False,
            sessions_processed=0, sessions_skipped=0, sessions_incomplete=0,
            sessions_needs_review=0, sessions_failed=0, errors=(), events=(),
        )])
        with redirect_stdout(io.StringIO()):
            code = main(["--program", "Programa"], runner_factory=lambda programs, state: runner)
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, ["Programa"])

    def test_empty_registry_fails_without_initializing_google(self):
        with tempfile.TemporaryDirectory() as directory:
            programs = Path(directory) / "programs.json"
            programs.write_text("{}", encoding="utf-8")
            with patch.object(CLI_MAIN_MODULE, "get_google_services_with_docs",
                       side_effect=AssertionError("Google must not initialize")):
                output = io.StringIO()
                with redirect_stderr(output):
                    code = main(["--programs-path", str(programs),
                                 "--state-path", str(Path(directory) / "state.json")])
        self.assertEqual(code, 1)
        self.assertIn("ejecuta participacion-init", output.getvalue())

    def test_refresh_error_is_concise_and_uses_runtime_failure_event(self):
        output = io.StringIO()
        with patch.object(CLI_MAIN_MODULE, "notify_events") as notify:
            with redirect_stderr(output):
                code = main([], runner_factory=lambda programs, state:
                            (_ for _ in ()).throw(RefreshError("invalid_scope")))
        self.assertEqual(code, 1)
        self.assertIn("participacion-google-auth", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())
        self.assertEqual(notify.call_args.args[1][0].event_type, "runtime.failed")

    def test_unexpected_programming_error_is_not_swallowed(self):
        with self.assertRaises(AssertionError):
            main([], runner_factory=lambda programs, state:
                 (_ for _ in ()).throw(AssertionError("programming defect")))

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
        with redirect_stderr(output):
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
        with patch.object(CLI_MAIN_MODULE, "load_programs", return_value={"program": program}), \
             patch.object(CLI_MAIN_MODULE, "get_google_services_with_docs", return_value=services), \
             patch.object(CLI_MAIN_MODULE, "_sheet_ids", return_value={}), \
             patch.object(CLI_MAIN_MODULE, "ParticipantRepository", return_value=repositories[0]), \
             patch.object(CLI_MAIN_MODULE, "SessionResultsRepository"), \
             patch.object(CLI_MAIN_MODULE, "RankingRepository"), \
             patch.object(CLI_MAIN_MODULE, "GoogleSheetsTrackingGateway"), \
             patch.object(CLI_MAIN_MODULE, "TrackingRepository"), \
             patch.object(CLI_MAIN_MODULE, "ControlRepository"), \
             patch.object(CLI_MAIN_MODULE, "FollowUpRepository"), \
             patch.object(CLI_MAIN_MODULE, "SessionProcessor", return_value=processor) as processor_constructor, \
             patch.object(CLI_MAIN_MODULE, "GoogleSheetsTrackingGateway"):
            runner = build_runner()
            dependencies = runner.dependencies_factory("program", program)
        self.assertIs(processor_constructor.call_args.kwargs["resolver_factory"].__func__, ParticipantResolver.auto.__func__)


if __name__ == "__main__":
    unittest.main()
