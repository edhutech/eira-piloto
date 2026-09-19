from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "participacion"


class PilotArchitectureTests(unittest.TestCase):
    def test_generic_pilot_components_have_no_client_named_adapter(self) -> None:
        self.assertFalse((SRC / "adapters" / "pilot" / "basf_source.py").exists())
        for path in (SRC / "application" / "pilot").glob("*.py"):
            text = path.read_text(encoding="utf-8").casefold()
            self.assertNotIn("basf", text, str(path))

    def test_runner_does_not_import_outcomes_or_google_adapters(self) -> None:
        tree = ast.parse((SRC / "application" / "pilot" / "runner.py").read_text(encoding="utf-8"))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any("google" in item or "outcome" in item for item in imports))

    def test_retrospective_is_separate_from_runner(self) -> None:
        runner = (SRC / "application" / "pilot" / "runner.py").read_text(encoding="utf-8")
        retrospective = (SRC / "application" / "pilot" / "results.py").read_text(encoding="utf-8")
        self.assertNotIn("BAJAS", runner)
        self.assertIn("OutcomeRecord", retrospective)

    def test_local_configuration_and_real_data_are_ignored(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for entry in ("configs/local/", "pilot-results/", "data/", "*.pilot.json"):
            self.assertIn(entry, gitignore)
        tracked = __import__("subprocess").run(
            ["git", "ls-files"], cwd=ROOT, text=True, capture_output=True, check=True
        ).stdout.splitlines()
        self.assertFalse(any("BASF - MGMT.xlsx" in item for item in tracked))

    def test_example_config_is_client_neutral(self) -> None:
        config = json.loads((ROOT / "configs" / "pilot.example.json").read_text(encoding="utf-8"))
        self.assertNotIn("basf", json.dumps(config).casefold())
        self.assertEqual(config["signals"]["version"], "pilot.v1")


if __name__ == "__main__":
    unittest.main()
