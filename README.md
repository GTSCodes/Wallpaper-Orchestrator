# Wallpaper Orchestrator

A multi-monitor wallpaper manager for Windows with collections, per-display
assignments, automatic shuffle intervals, calendar rules and a separate
background agent. The app is built with Python, PySide6, SQLite, Pillow and
Windows `IDesktopWallpaper` COM.

## Running from source (Windows)

Use Python 3.12 or newer:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[gui,dev]'
python desktop.py
```

The source-mode background process uses the `pythonw.exe` in the active
virtual environment. To build a standalone folder:

```powershell
.\scripts\build_windows.ps1
```

To produce an Inno Setup installer after building the portable version:

```powershell
.\scripts\build_installer.ps1
```

See `docs/C8-WINDOWS-TESTING.md` for clean-install, agent and upgrade tests.
User wallpapers and settings are saved separately from the application binaries
and are not removed by the uninstaller.
