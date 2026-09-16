import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1] / "src" / "participacion"


def _module_for(path: Path) -> str:
    return "participacion." + ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _imported_module(node: ast.ImportFrom, current: str) -> str:
    if node.level == 0:
        return node.module or ""
    package = current.split(".")[:-1]
    prefix = package[:len(package) - node.level + 1]
    return ".".join(prefix + ((node.module or "").split(".") if node.module else []))


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_import_graph_respects_layer_direction(self):
        forbidden = {
            "core": {"application", "addons", "adapters", "cli"},
            "application": {"addons", "adapters", "cli"},
            "addons": {"adapters", "cli"},
            "adapters": {"cli"},
        }
        for path in ROOT.rglob("*.py"):
            current = _module_for(path)
            layer = path.relative_to(ROOT).parts[0]
            if layer not in forbidden:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [_imported_module(node, current)]
                else:
                    continue
                for module in modules:
                    parts = module.split(".")
                    if parts[:1] != ["participacion"] or len(parts) < 2:
                        continue
                    target = parts[1]
                    if target in forbidden[layer]:
                        self.fail(f"{path}: import {module} violates {layer} -> {target}")

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
