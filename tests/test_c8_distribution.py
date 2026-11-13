"""C8 can start a standalone agent without relying on installed Python."""

import unittest
from pathlib import Path
from unittest.mock import patch

from wallpaper_orchestrator import background, desktop_entry


class DistributionTests(unittest.TestCase):
    def test_compiled_agent_uses_executable_with_mode(self):
        exe = Path(r"C:\Program Files\Wallpaper Orchestrator\WallpaperOrchestrator.exe")
        self.assertEqual(background.agent_launch_arguments(
            compiled=True, executable=exe),
            [str(exe), "--background-agent"])

    def test_source_agent_uses_pythonw_and_source_script(self):
        interpreter = Path(r"C:\repo\.venv\Scripts\pythonw.exe")
        source = Path(r"C:\repo\src\wallpaper_orchestrator\background.py")
        self.assertEqual(background.agent_launch_arguments(
            compiled=False, executable=interpreter, script=source),
            [str(interpreter), str(source)])

    def test_dispatch_is_explicit_and_does_not_launch_unexpected_modes(self):
        with patch.object(desktop_entry, "_run_background", return_value=12) as agent:
            self.assertEqual(desktop_entry.main(["--background-agent"]), 12)
            agent.assert_called_once_with()
        with patch.object(desktop_entry, "_run_gui", return_value=0) as gui:
            self.assertEqual(desktop_entry.main([]), 0)
            gui.assert_called_once_with()
        with self.assertRaises(SystemExit):
            desktop_entry.main(["--background-agent", "--self-test"])


if __name__ == "__main__":
    unittest.main()
