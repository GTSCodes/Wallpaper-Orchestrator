# Wallpaper Orchestrator — C8.1 standalone Windows build

This milestone produces a **portable Windows folder** containing
`WallpaperOrchestrator.exe` and all required runtime files, with **no Python
installation needed on the end-user computer**. It is the foundation for
C8.2's installer and first-run wizard. It is not yet an installer.

## Why the architecture changes

C7's independent background worker runs `pythonw.exe background.py`. That is
correct for source development, but a bundled application cannot assume that
Python or a virtual environment exists. C8.1 makes one compiled application
support two explicit modes:

| Mode | Action |
| --- | --- |
| Double-click `WallpaperOrchestrator.exe` | Opens the GUI and system tray |
| `WallpaperOrchestrator.exe --background-agent` | Runs the quiet independent Windows worker; no GUI |
| `WallpaperOrchestrator.exe --self-test` | Checks bundled imports without changing user configuration |
| `WallpaperOrchestrator.exe --disable-background` | Explicitly turns off background mode (useful for maintenance) |

When running from source, the existing `pythonw.exe background.py` path remains
unchanged. In a Nuitka build, the agent launches the **same compiled exe** with
the `--background-agent` argument. The two execution paths use the same
`%LOCALAPPDATA%\WallpaperOrchestrator` data directory and singleton mutex.

There is no new installer-managed or extra copy of your catalogue database.

## Build on Windows (developer)

Prerequisites on the **build machine only**:

- Windows 10/11, Python 3.12 or later, a virtual environment and MSVC C/C++
  build tools (normally available on a Windows GitHub Actions runner).
- Internet access for installing project dependencies and Nuitka.

From the extracted repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
.\scripts\build_windows.ps1
```

The build script installs `.[gui]`, Nuitka and build dependencies, runs all
unit tests, creates a simple Windows icon, then compiles with Nuitka's
**standalone** mode and PySide6 plugin. It refuses to overwrite a previous
`dist\WallpaperOrchestrator` folder; move that aside before a repeat build.

Successful result:

```text
dist/
  WallpaperOrchestrator/
    WallpaperOrchestrator.exe
    (Qt / Python runtime dependencies)
```

Copy the **entire** `WallpaperOrchestrator` folder to another Windows machine,
then double-click the `.exe`. Do not distribute the `.exe` alone: it needs its
adjacent bundled DLLs and runtime files.

### GitHub Actions build (no compiler installation on your PC)

Push the code to a branch named `feature/c8-distribution` (or another
`feature/c8-*` branch). The workflow runs automatically for that branch;
download the artifact named `WallpaperOrchestrator-C8.1-Windows` from the
resulting **Actions → C8 Windows portable build** run. Once the workflow file
is merged into your default branch, the **Run workflow** button can also
trigger a manual build. GitHub does not show the manual button for a workflow
that exists only on a feature branch.

The ZIP contains the entire standalone directory. The workflow installs
dependencies on a Windows runner, executes tests, builds with Nuitka and
checks imports from the compiled binary before uploading. It does **not**
publish a release or deploy an installer automatically.

## Manual Windows acceptance checklist

**Important:** Don't run a source C7 agent and the packaged C8 agent together.
First disable background mode in your currently running C7 GUI, wait at least
20 seconds, and exit its GUI/tray. Back up
`%LOCALAPPDATA%\WallpaperOrchestrator` before trying a packaged build against
your existing catalogue. These steps avoid the old-agent/version mismatch you
encountered during C6/C7.

| Test | Action | Expected result |
| --- | --- | --- |
| 1. Fresh launch | Run the standalone exe from its folder, not the Python project. | GUI and system tray appear without a terminal or Python install. |
| 2. Move folder | Copy the complete standalone directory to a folder with spaces in its path; launch there. | GUI starts; Qt plugins and imports work. |
| 3. Library compatibility | Browse existing collections, tags and 4-image Test collection. | User database is preserved, no schema-version warning. |
| 4. Manual strict shuffle | Select landscape and shuffle. | Only eligible images from its saved Test collection rule. |
| 5. Background independence | Enable background mode from GUI. Close GUI with tray **Exit GUI**. Wait longer than an interval. | Background worker continues applying wallpapers without a Python interpreter. |
| 6. Login startup | Check the per-user startup setting in Task Manager or Windows Settings and sign out/in after enabling. | Background agent starts without launching a GUI window. |
| 7. Disable safely | From the packaged GUI, turn background mode off and wait ~20s. | The agent stops and the login startup command is unregistered. |
| 8. Failure transparency | Temporarily point a profile at a missing folder, then attempt a manual shuffle. | Clear error, existing wallpaper unchanged; no crash. |

For a headless compiled import check, use:

```powershell
.\dist\WallpaperOrchestrator\WallpaperOrchestrator.exe --self-test
$LASTEXITCODE
```

An exit code of `0` means expected modules were bundled, **not** that Windows
monitor COM calls or live scheduling passed. Tests 1–8 remain essential.

## What is *not* done yet

- No Windows installer/uninstaller (C8.2).
- No first-run onboarding wizard or automatic settings migration beyond the
  existing compatible C7 data format (C8.2).
- No code signing or public release publication (C8.3).
- No macOS/Linux build (C9), and no major GUI redesign (C9+).
- No standalone `.exe` has been compiled in this Linux-based workspace; the
  Windows build script/CI must run and the result must pass manual acceptance.

## Dependencies and implementation

- **PySide6 (Qt for Python):** GUI, tray icon, Qt widgets and graphics.
- **Nuitka:** compiles Python into a standalone Windows application folder.
- **Pillow:** wallpaper image handling and build-time icon creation.
- **comtypes:** Windows `IDesktopWallpaper` COM interface for per-monitor
  application; the bundled worker uses the exact same interface.
- **GitHub Actions:** optional Windows CI builder and artifact delivery.

Qt's `pyside6-deploy` is a higher-level Nuitka wrapper and remains a valid
future option. This phase invokes Nuitka directly to control the executable's
special background-agent entry point and smoke test explicitly.

Primary documentation:

- [Qt for Python deployment](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html)
- [Nuitka standalone guidance](https://nuitka.net/tours/deployment.html)
- [Nuitka compilation detection](https://nuitka.net/user-documentation/tips.html)
- [GitHub Actions for Python](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)
