"""C8 executable dispatcher: GUI and headless agent share one distributable.

The imports remain lazy so a background worker never opens a GUI window.
"""

import os
from pathlib import Path
import sys
import traceback


def _record_startup_failure(component: str, error: BaseException):
    """Report failures even when Windows has no console attached."""
    try:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config")
        directory = base / "WallpaperOrchestrator"
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / f"{component}-startup-error.txt"
        log.write_text("".join(traceback.format_exception(error))[-10000:],
                       encoding="utf-8")
    except OSError:
        pass


def _run_gui() -> int:
    try:
        from wallpaper_orchestrator.gui import main as run_gui
        return run_gui()
    except Exception as error:
        _record_startup_failure("gui", error)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication([sys.argv[0]])
            QMessageBox.critical(None, "Wallpaper Orchestrator could not start",
                                 f"{error}\n\nDetails were saved in the app's local settings folder.")
        except Exception:
            pass
        return 1


def _run_background() -> int:
    try:
        from wallpaper_orchestrator import background
        return background.main()
    except Exception as error:
        _record_startup_failure("background", error)
        try:
            from wallpaper_orchestrator.background import _write_status
            _write_status(f"Background agent could not start: {error}", running=False)
        except Exception:
            pass
        return 1


def _self_test() -> int:
    """Import-only smoke check for the standalone bundle; no user state writes."""
    from PIL import Image  # noqa: F401
    from PySide6.QtCore import QCoreApplication  # noqa: F401
    from PySide6.QtWidgets import QApplication  # noqa: F401
    from wallpaper_orchestrator.platforms.windows import IDesktopWallpaper  # noqa: F401
    from wallpaper_orchestrator.gui import WallpaperWindow  # noqa: F401
    from wallpaper_orchestrator.background import agent_launch_arguments
    assert agent_launch_arguments(compiled=True)[-1] == "--background-agent"
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _run_gui()
    if args == ["--background-agent"]:
        return _run_background()
    if args == ["--self-test"]:
        return _self_test()
    if args == ["--disable-background"]:
        try:
            from wallpaper_orchestrator.background import set_background_enabled
            set_background_enabled(False)
            return 0
        except Exception as error:
            _record_startup_failure("background", error)
            return 1
    raise SystemExit(
        "Usage: WallpaperOrchestrator.exe [--background-agent | --self-test | --disable-background]"
    )
