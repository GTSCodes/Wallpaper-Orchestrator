"""Regression: the per-display editor must identify monitors, not just their orientation.

The original hotfix updated the group editor, but accidentally left the
IntelligenceDialog's per-display dropdown with only landscape/portrait labels.
This is a source-structure test because CI might not provide a windowing server.
"""

import ast
from pathlib import Path
import unittest


class MonitorDialogWiringTests(unittest.TestCase):
    def test_per_display_dropdown_uses_unique_labels_and_stable_ids(self):
        source_path = (Path(__file__).resolve().parents[1] / "src" /
                       "wallpaper_orchestrator" / "intelligence_view.py")
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        dialog = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == "IntelligenceDialog")
        constructor = next(node for node in dialog.body
                           if isinstance(node, ast.FunctionDef) and node.name == "__init__")
        calls = [node for node in ast.walk(constructor)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == "monitor_labels"]
        self.assertTrue(calls, "Per-display rules dropdown must use monitor_labels")
        self.assertTrue(any(len(call.args) == 2 for call in calls))
        choices = [node for node in ast.walk(constructor)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "addItem"
                   and isinstance(node.func.value, ast.Attribute)
                   and node.func.value.attr == "monitor_choice"]
        self.assertEqual(len(choices), 1)
        self.assertEqual(ast.unparse(choices[0].args[0]), "label")
        self.assertEqual(ast.unparse(choices[0].args[1]), "monitor.device_id")


if __name__ == "__main__":
    unittest.main()
