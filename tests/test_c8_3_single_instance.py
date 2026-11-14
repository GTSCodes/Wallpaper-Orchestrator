"""Windows/Qt IPC regression: source and packaged launches share one GUI."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HAS_QT = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(HAS_QT, "PySide6 is required for the Windows GUI IPC")
class GuiInstanceTests(unittest.TestCase):
    def test_second_launch_activates_existing_and_upgrade_quits(self):
        from wallpaper_orchestrator.gui_single_instance import send_command, server_name
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            env = dict(os.environ, LOCALAPPDATA=str(target))
            with patch.dict(os.environ, {"LOCALAPPDATA": str(target)}):
                own_channel = server_name()
            with patch.dict(os.environ, {"LOCALAPPDATA": str(target / "other") } ):
                self.assertNotEqual(server_name(), own_channel)
            script = '''
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from wallpaper_orchestrator.gui_single_instance import GuiInstanceGateway
import os
root = Path(os.environ["LOCALAPPDATA"])
app = QCoreApplication([])
def show():
    (root / "activated").write_text("yes")
def quit_now():
    app.quit()
server = GuiInstanceGateway(on_show=show, on_quit=quit_now)
assert server.claim()
(root / "ready").write_text("yes")
raise SystemExit(app.exec())
'''
            child = subprocess.Popen([sys.executable, "-c", script], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 8
                while not (target / "ready").exists() and time.monotonic() < deadline:
                    if child.poll() is not None:
                        self.fail("GUI IPC worker exited during startup")
                    time.sleep(0.05)
                self.assertTrue((target / "ready").exists())
                with patch.dict(os.environ, {"LOCALAPPDATA": str(target)}):
                    self.assertTrue(send_command("SHOW", timeout_ms=2000))
                    deadline = time.monotonic() + 3
                    while not (target / "activated").exists() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue((target / "activated").exists())
                    self.assertTrue(send_command("QUIT", timeout_ms=2000))
                self.assertEqual(child.wait(timeout=7), 0)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
