import io
import importlib
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from google.auth.exceptions import RefreshError

from participacion.cli.main import main

CLI_MAIN_MODULE = importlib.import_module("participacion.cli.main")


class FakeRunner:
    def __init__(self, results):
        self.results = results

    def run(self, program_id=None):
        return self.results


def result(**overrides):
    base = dict(
        program_name="Program",
        session_results=[],
        ranking_changed=False,
        sessions_processed=1,
        sessions_skipped=0,
        sessions_incomplete=0,
        sessions_needs_review=0,
        sessions_failed=0,
        warnings=(),
        errors=(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class CliTests(unittest.TestCase):
    def test_success_is_explicit_in_terminal(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([], runner_factory=lambda programs, state: FakeRunner([result()]))
        self.assertEqual(code, 0)
        self.assertIn("RESULT: SUCCESS", output.getvalue())
        self.assertIn("processed=1", output.getvalue())

    def test_runner_failure_is_detailed_and_returns_one(self):
        session = SimpleNamespace(
            session_number=1,
            status=SimpleNamespace(value="FAILED"),
            warnings=(),
            errors=("synthetic failure",),
        )
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main([], runner_factory=lambda programs, state: FakeRunner([
                result(session_results=[session], sessions_processed=0,
                       sessions_failed=1, errors=("synthetic failure",))
            ]))
        self.assertEqual(code, 1)
        self.assertIn("synthetic failure", output.getvalue())
        self.assertIn("RESULT: ERROR", error.getvalue())

    def test_empty_registry_fails_without_initializing_google(self):
        with tempfile.TemporaryDirectory() as directory:
            programs = Path(directory) / "programs.json"
            programs.write_text("{}", encoding="utf-8")
            with patch.object(
                CLI_MAIN_MODULE,
                "get_google_services_with_docs",
                side_effect=AssertionError("Google must not initialize"),
            ):
                error = io.StringIO()
                with redirect_stderr(error):
                    code = main([
                        "--programs-path", str(programs),
                        "--state-path", str(Path(directory) / "state.json"),
                    ])
        self.assertEqual(code, 1)
        self.assertIn("RESULT: ERROR", error.getvalue())

    def test_google_auth_error_is_concise(self):
        error = io.StringIO()
        with redirect_stderr(error):
            code = main(
                [],
                runner_factory=lambda programs, state: (
                    _ for _ in ()
                ).throw(RefreshError("invalid_scope")),
            )
        self.assertEqual(code, 1)
        self.assertIn("participacion-google-auth", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    def test_programming_error_is_reported_then_propagated(self):
        error = io.StringIO()
        with redirect_stderr(error):
            with self.assertRaises(AssertionError):
                main(
                    [],
                    runner_factory=lambda programs, state: (
                        _ for _ in ()
                    ).throw(AssertionError("programming defect")),
                )
        self.assertIn("unexpected AssertionError", error.getvalue())


if __name__ == "__main__":
    unittest.main()
