import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from participacion.cli import main


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
            sessions_needs_review=0, sessions_failed=0, errors=(),
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
                sessions_needs_review=0, sessions_failed=failed, errors=errors,
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


if __name__ == "__main__":
    unittest.main()
