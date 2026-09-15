import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1] / "src" / "participacion"


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_core_and_application_have_no_infrastructure_imports(self):
        forbidden = ("googleapiclient", "google.oauth", "subprocess", "notify-send")
        for layer in ("core", "application"):
            for path in (ROOT / layer).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                self.assertFalse(any(value in text for value in forbidden), path)

    def test_no_legacy_src_imports_remain(self):
        for path in ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [alias.name for alias in node.names]
                    module = node.module if isinstance(node, ast.ImportFrom) else ""
                    self.assertNotIn("src", (module or "").split("."), path)
                    self.assertTrue(all(not name.startswith("src.") for name in names), path)


if __name__ == "__main__":
    unittest.main()
