"""Portable regression tests for monitor identity handling (no Windows/Qt needed)."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

SRC = Path(__file__).resolve().parents[1] / 'src' / 'wallpaper_orchestrator'


def load_function(path, name, namespace):
    tree = ast.parse((SRC / path).read_text(encoding='utf-8'))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    code = compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec')
    exec(code, namespace)
    return namespace[name]


class MonitorIdentityTests(unittest.TestCase):
    def test_discovery_filters_duplicate_and_disconnected_ids(self):
        ids = ['portrait', 'landscape', 'landscape', 'offline', 'laptop', '']
        class Desktop:
            def GetMonitorDevicePathCount(self): return len(ids)
            def GetMonitorDevicePathAt(self, index): return ids[index]
            def GetMonitorRECT(self, device_id):
                return SimpleNamespace(left=0, top=0, right=0 if device_id == 'offline' else 1920, bottom=1080)
        discover = load_function('platforms/windows.py', 'discover_monitors', {
            'CreateObject': lambda *a, **k: Desktop(), 'IDesktopWallpaper': object,
            'DetectedMonitor': SimpleNamespace, 'COMError': RuntimeError,
        })
        self.assertEqual([m.device_id for m in discover()], ['portrait', 'landscape', 'laptop'])

    def test_invalid_first_entry_does_not_hide_valid_duplicate(self):
        class Desktop:
            calls = 0
            def GetMonitorDevicePathCount(self): return 2
            def GetMonitorDevicePathAt(self, index): return 'landscape'
            def GetMonitorRECT(self, device_id):
                self.calls += 1
                return SimpleNamespace(left=0, top=0, right=1920 if self.calls == 2 else 0, bottom=1080)
        desktop = Desktop()
        discover = load_function('platforms/windows.py', 'discover_monitors', {
            'CreateObject': lambda *a, **k: desktop, 'IDesktopWallpaper': object,
            'DetectedMonitor': SimpleNamespace, 'COMError': RuntimeError,
        })
        self.assertEqual(len(discover()), 1)

    def test_gui_selection_uses_unique_rendered_ids(self):
        tree = ast.parse((SRC / 'gui.py').read_text(encoding='utf-8'))
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'monitor_selected')
        # Execute just the selection assignment, independent of Qt widgets.
        code = compile(ast.Module(body=[method.body[0]], type_ignores=[]), 'gui.py', 'exec')
        window = SimpleNamespace(monitors=[SimpleNamespace(device_id='landscape')]*2,
            monitor_items={'landscape': SimpleNamespace(isSelected=lambda: True),
                           'portrait': SimpleNamespace(isSelected=lambda: False)})
        exec(code, {'self': window})
        self.assertEqual(window.selected_device_ids, ['landscape'])

    def test_both_planners_deduplicate_discovery(self):
        tree = ast.parse((SRC / 'engine.py').read_text(encoding='utf-8'))
        for name in ('create_plan', 'create_profile_plan'):
            with self.subTest(name=name):
                method = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
                statement = next(n for n in method.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'detected' for t in n.targets))
                ns = {'discover_monitors': lambda: [SimpleNamespace(device_id='a'), SimpleNamespace(device_id='a'), SimpleNamespace(device_id='b')]}
                exec(compile(ast.Module(body=[statement], type_ignores=[]), 'engine.py', 'exec'), ns)
                self.assertEqual([m.device_id for m in ns['detected']], ['a', 'b'])


if __name__ == '__main__':
    unittest.main()
