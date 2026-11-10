# Wallpaper Orchestrator — C3.1 automation reliability update

This **small update builds on C3**, the Displays > Automate interval feature
and independent Windows background agent. It addresses a case where the
wallpapers change when you press **Update displays** but never change on the
next timed interval. It keeps exactly the same Displays layout; no additional
rows or settings panels were introduced.

## Install

Back up or commit your existing changes. Extract the C3.1 ZIP into the root of
your existing project and merge these files with local edits if needed:

```
src/wallpaper_orchestrator/background.py           REPLACE
src/wallpaper_orchestrator/automation_view.py      REPLACE
src/wallpaper_orchestrator/gui.py                  REPLACE
src/wallpaper_orchestrator/engine.py               REPLACE
tests/test_automation.py                          REPLACE
tests/test_collection_selection.py                REPLACE
README_C3_1.md
```

The ZIP is **incremental**: it needs the C3 modules and the earlier B1–C2
project files. It does not include or reset any user's `profiles.json`,
`automation.json`, SQLite database, folders, tags, or scheduled rules.

Restart the GUI:

```powershell
$env:PYTHONPATH = "src"
python -m wallpaper_orchestrator.gui
```

## What changed

1. **No more silent GUI timer shutdown:** enabling background mode used to
   make the GUI's 20-second schedule check return immediately, *even when the
   hidden agent wasn't running*. Now, if the agent is absent, the GUI uses
   the same due-rule executor for interval and time-of-day rules. A Windows
   named mutex ensures the GUI cannot run alongside an active agent.
2. **Better error reporting:** the Automate popup now shows the latest agent
   health result and Displays' existing bottom status text shows agent
   startup status shortly after saving. If `pythonw.exe` crashes before its
   normal loop starts, it writes the exception to the agent status JSON.
3. **Diagnostic CLI:** the read-only `--status` mode shows whether background
   mode is enabled, whether a recent heartbeat was received, the number of
   saved interval rules, their next due times, and their last outcomes.
4. **Random really shuffles:** if a monitor has more than one available image,
   Random prioritizes an image different from the one currently displayed.
   Other constraints may still leave only the same viable image.

## Please run this exact test on Windows

1. In **Displays**, select the two monitors, then open **Automate**.
2. Set **1 minute**, keep **Run even when the application window is closed**
   checked, and click **Save automation**. Ensure the status shows that the
   automation was saved (not that it was removed).
3. In VS Code PowerShell, run:

```powershell
$env:PYTHONPATH = "src"
python -m wallpaper_orchestrator.background --status
```

   You should see `Background enabled: True`, `Saved interval rules: 1`
   (or more, if other monitor groups have their own automation), and
   `Next due (local)` about a minute ahead. If you see `0`, the interval
   was removed or was never saved.

4. Wait **80–90 seconds** with the app open, then run the status command
   again. Expect the wallpaper to change and the rule's `Last result` to say
   `Applied ...`. You may also check the agent status file:

```powershell
Get-Content "$env:LOCALAPPDATA\WallpaperOrchestrator\background-status.json"
```

5. Close the app completely, then wait another **80–90 seconds**. The
   wallpaper should still change if the hidden agent is running. Rerun
   the status command. A failure message or stale heartbeat will now be
   visible in the diagnostic output.

**If the agent stays offline while the app is closed**, send the output of
`--status` and the `background-status.json` message. Background startup still
depends on your Python environment, Windows session, registry startup entry,
and Windows COM. A fallback while the GUI is open does not solve a failed
headless startup after you close it.

For a foreground diagnostic *only when no other agent holds the singleton*:

```powershell
python src\wallpaper_orchestrator\background.py
```

This runs continuously and will apply the due rules. Press Ctrl+C to stop;
it does not stop other background agents. Prefer `--status` first because it
does not start or change anything.

## Implementation details

| Code | Reason |
| --- | --- |
| `background.py::agent_health_text()` | Summarizes heartbeat and last outcome for the GUI. |
| `background.py::diagnostic_lines()` | Read-only inspection of interval settings and status from PowerShell. |
| `background.py::apply_due_in_foreground_if_agent_missing()` | Lets GUI execute due automation only when it can acquire the agent's Windows singleton mutex. |
| `background.py::_report_unhandled_error()` | Captures otherwise silent `pythonw.exe` import/startup crashes in the status JSON. |
| `gui.py::_schedule_tick()` | Falls back to applying due rules when the background process isn't responding. |
| `gui.py::_report_agent_startup()` | Updates the existing status line without expanding the Displays screen. |
| `automation_view.py::AutomateDialog` | Shows the last health message in the existing modal. |
| `engine.py::_order_candidates()` | Avoids picking the currently visible wallpaper first in Random mode where alternatives exist. |

The Windows-specific pieces use the existing **`ctypes`** COM/Win32 mutex
access, **`winreg`** startup integration, and **`subprocess`** `pythonw.exe`
launcher. `datetime` and `time` handle interval timing, while **PySide6**
renders the small health label without changing the Displays layout.

## Verification

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

85 unit tests passed in the Linux development environment, including new
agent-status, fallback-mutex and random-selection checks. Actual Win32 COM,
background process longevity and Windows Run startup must still be verified
on your Windows machine.
