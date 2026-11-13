# C8: Windows build, installation and acceptance tests

## What this source package contains

- `desktop.py`: standalone Nuitka entry point restored from the ZIP omission.
- `src/wallpaper_orchestrator/bundle_identity.py`: defaults to *source mode*.
  `scripts/build_windows.ps1` temporarily sets its marker true **inside the
  compiled bundle**, then restores the original file even if compilation fails.
- `background.py`: executable validation, launch traceback logging, named
  singleton mutex, cooperative named stop event for installer upgrades.
  On Windows, GetModuleFileNameW retrieves the real process EXE because Nuitka
  may expose a nonexistent `python.exe` through `sys.executable`.
- `desktop_entry.py`: GUI, headless agent, `--self-test`, `--repair-startup`,
  `--stop-agent`, and legacy `--disable-background` dispatch.
- `setup_wizard.py`: optional first-run setup integrated into the Setup menu.
- `installer/WallpaperOrchestrator.iss` and `scripts/build_installer.ps1`:
  per-user installer sources; **not** a compiled installer.

## 1. Inspect the Git worktree first

The user-supplied ZIP **did not include `.git`**. If your original repository
is available, back it up/commit existing changes before replacing source files.
From your actual repository root:

```powershell
git status --short
git branch --show-current
git switch -c feature/c8-agent-installer
```

Merge these reviewed files into your real repository, preserving any newer
changes made since the uploaded ZIP. Do not blindly overwrite your user
settings, SQLite catalogue or wallpapers. The application data is not part of
this source package.

## 2. Run source tests on Windows

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[gui,dev]'
python -m unittest discover -s tests -q
```

A passing unit-test suite does **not** establish that the packaged process
starts correctly. It checks command construction, registry actions via mocks,
workflow routing, compatibility, and previous monitor/interval regressions.

## 3. Build the C8.1 portable executable

```powershell
.\scripts\build_windows.ps1
$exe = (Resolve-Path '.\dist\WallpaperOrchestrator\WallpaperOrchestrator.exe').Path
$p = Start-Process -FilePath $exe -ArgumentList '--self-test' -Wait -PassThru
$p.ExitCode
```

Expect exit code **0**. The self-test checks the bundle marker, the actual
Windows process-image path and whether that executable exists. If it returns
`1`, inspect `%LOCALAPPDATA%\WallpaperOrchestrator\self-test-startup-error.txt`
for a full traceback before attempting to enable background automation.
The build script performs this check before publishing its portable folder.
Move/copy the **whole standalone folder**, not just its .exe, to a different
path with spaces and repeat the smoke test. Before doing this, completely exit
old GUI and agent copies; only one copy should own the background-agent mutex.

## 4. Confirm the actual packaged child (most important test)

1. Launch `WallpaperOrchestrator.exe` from the *copied* standalone folder.
2. In **Schedules → Manage all automations**, enable **Keep all enabled
   automations running after the app window closes**.
3. Create a short interval (e.g. one minute) on a non-fixed display with
   several wallpapers in its selected folder. Do not reuse a conflicting
   interval; the app automatically pauses older overlapping rules.
4. Completely exit the GUI **using the tray's Exit GUI action**, rather than
   just closing the window to the tray.
5. In PowerShell, run:

   ```powershell
   .\scripts\verify_windows_agent.ps1 -Executable 'C:\Path With Spaces\WallpaperOrchestrator.exe'
   ```

   Expect one command-line `--background-agent` process whose executable path
   is the standalone app, a matching HKCU Run key and an advancing heartbeat.
6. Wait for at least one interval and visibly confirm a wallpaper change on
   the enabled display(s) while the GUI is fully closed.
7. Restart Windows, sign in and repeat the script/check. A healthy startup
   must not depend on `.venv` or the original project directory.
8. Disconnect one screen; verify connected screens continue receiving updates.
   Reconnect it and verify it is retained in the saved automation rules.
9. Repeatedly launch the GUI and enable background mode; verify only one
   packaged agent is active (the GUI may still allow extra instances until
   C8.3).

The read-only script checks the saved opt-in before running. It does not
change wallpapers or edit your preferences.

### Diagnosing a failure

Check these files in `%LOCALAPPDATA%\WallpaperOrchestrator`:

- `background-launch.log`: launcher path, runtime mode, command, working
  directory, child PID or complete process creation traceback.
- `background-status.json`: worker heartbeat and latest message.
- `background-startup-error.txt`: exceptions caught in the packaged dispatcher.

A `created child PID` entry is **not** proof that the agent fully started:
look for an advancing heartbeat, and verify wallpaper application after
exiting the GUI. If the first error is WinError 2, inspect the *logged command*
for an accidental `pythonw.exe` path or stale installation. If launch succeeds
but heartbeat does not advance, inspect the dispatcher startup error file.

## 5. Build the C8.2 installer

Install **Inno Setup 6** on your Windows build machine. After a successful
C8.1 build and packaged-agent acceptance test, run:

```powershell
.\scripts\build_installer.ps1
```

This generates `dist\WallpaperOrchestrator-Setup.exe`, if the script compiles
successfully on Windows. The graphical installer uses a per-user folder:
`%LOCALAPPDATA%\Programs\WallpaperOrchestrator`. No Python, terminal or
administrator privileges should be required for end users. The optional
Desktop shortcut is unchecked by default.

The installer starts the app only if the user ticks the finish-page option.
On a genuinely new user profile, the setup wizard asks about library folder,
detected displays and optional automatic shuffle. Existing profiles/settings
bypass the first-run wizard. The Setup menu can reopen it.

## 6. Installer acceptance checklist

- [ ] Install as a fresh non-admin Windows user without Python installed.
- [ ] Launch from Start Menu and optional Desktop shortcut.
- [ ] Run setup wizard; leave automation off; confirm current wallpaper is not changed.
- [ ] Configure a main folder and per-monitor overrides; restart GUI and confirm persistence.
- [ ] Opt into interval automation; exit GUI and verify a real agent process and change.
- [ ] Confirm installed path, not portable/development path, is in HKCU Run.
- [ ] Restart Windows and verify scheduled changes continue.
- [ ] Upgrade the installed version, preserving catalogue, tags, profiles and rules.
- [ ] Test path with spaces and re-run agent self-test.
- [ ] Uninstall; check that background agent exits and the installed app's startup entry is removed.
- [ ] Confirm wallpapers and `%LOCALAPPDATA%\WallpaperOrchestrator` remain intact.
- [ ] Reinstall and verify existing settings are reused without forcing setup.

Do not perform the uninstall/update tests while a duplicate older portable GUI
is running. On pre-C8.2 upgrades, the previous agent does not understand
`--stop-agent`; the installer uses the Windows Restart Manager, and you may
need to manually stop the old process first.

## Validation status

Automated tests can run on non-Windows systems; Windows COM, the registry, Nuitka,
PySide6 GUI and Inno Setup cannot be fully integration-tested there. A build
and end-to-end acceptance test is **required** before C8.1/C8.2 can be marked
released.
