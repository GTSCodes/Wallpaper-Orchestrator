# Wallpaper Orchestrator — C7/C8.1 monitor automation hotfix

This is an **incremental Git patch** based on the C7 source uploaded in this conversation. It changes only the 8 listed code/test files and does not include your personal library, database, settings, wallpapers or previous builds. Use it **before** integrating C8.2. It is designed to preserve C8.1's packaged launcher, unlike copying an entire old `background.py` or `gui.py`.

## Fixes

1. A due three-monitor interval still updates the two connected monitors when the laptop display disappears. The disconnected member remains saved and rejoins automatically at the next due interval after reconnecting. If **no** saved members are connected, the interval remains deferred. Fixed-image monitors remain excluded.
2. **Interval automation exclusivity:** one enabled interval rule per monitor. Creating or editing a timer pauses any enabled interval that shares a monitor. Paused rules are kept (not deleted). Resuming one in the Automation Centre displays a confirmation describing the other timer(s) that will be paused. Existing overlapping interval rules are reconciled when the updated agent next runs (most recently saved enabled rule wins).
3. The C5 **Per-display rules** dropdown, monitor groups and interval editor show distinguishable labels with resolution, monitor device model/UID and a stable short ID, while saved policies continue to key off the full Windows monitor ID.

**Intentional scope:** C4 seasonal *filters* remain compatible with interval shuffles. Daily schedules and seasonal trigger definitions are managed separately; this patch does not impose exclusivity between a daily schedule and an interval.

## Install on the Windows C8.1 source checkout

1. Commit/backup your working project, and back up `%LOCALAPPDATA%\WallpaperOrchestrator` (the catalogue and settings are **not** in Git).
2. Disable background mode from the Automation Centre and verify the hidden agent has stopped. Close the GUI. You must later restart the agent because a previously running process still holds the old Python code.
3. Extract this ZIP into the **repository root** (the folder containing `src`, `tests`, and `scripts`). It adds only the `WPO_monitor_hotfix` directory containing the patch and this guide.
4. Open PowerShell from the repository root:

   ```powershell
   git switch -c fix/monitor-automation
   git apply --check .\WPO_monitor_hotfix\WPO_C8_1_monitor_hotfix.patch
   git apply .\WPO_monitor_hotfix\WPO_C8_1_monitor_hotfix.patch
   python -m unittest discover -s tests -q
   git diff --check
   ```

   If `git switch -c` says the branch exists, use another name. If `git apply --check` reports a conflict with the newer C8.1 source, **stop**, do not use `--force` or overwrite modules wholesale; share the output so the patch can be adapted to the exact checkout.

5. Relaunch from the updated source (`python -m wallpaper_orchestrator.gui`), restore background mode and test. To refresh your **standalone EXE**, move/rename the previous `dist\WallpaperOrchestrator` folder and run `.\scripts\build_windows.ps1` in PowerShell. Ensure the desktop shortcut points to the newly compiled build; an older executable will not contain these fixes.

6. Commit only after your Windows tests pass. The `.patch` file and this README can remain outside Git if you prefer.

**Windows lid behavior:** If closing the lid puts the entire PC to sleep, *no Python process can run while sleeping*. To use the external monitors while shut, Windows must remain awake: Control Panel > Hardware and Sound > Power Options > Choose what closing the lid does > **When I close the lid: Do nothing** (prefer plugged in). Consider battery use, heat and ventilation; this is a separate OS setting, not an application bug.

## Windows acceptance tests

- [ ] With portrait + landscape + laptop all in one 1-minute interval, close the lid **while Windows stays awake**. The two external monitors continue changing. Reopen: the laptop rejoins on the next due interval. No rule is deleted.
- [ ] Create a 1-minute portrait interval, then a 2-minute portrait + landscape interval. In Automation Centre, the old portrait interval is **Paused**, the new one is **Active**.
- [ ] Select the paused portrait interval and **Resume selected**. A confirmation warns that the portrait + landscape interval will pause. Choosing **No** preserves both statuses; choosing **Yes** swaps them.
- [ ] Create non-overlapping portrait and landscape intervals. Both remain active.
- [ ] In Intelligent wallpaper selection > Per-display rules, the two landscape screens show different resolution/device/ID labels. Configure different rules on each, save, reopen the dialog, and verify they persist on the correct screen.
- [ ] Check C5 strict `Require` constraints, fixed-image monitors, active seasonal filters, tray pause/resume, and automatic changes with the GUI closed.
- [ ] `python -m wallpaper_orchestrator.background --status` shows the newly running agent and no `Waiting for a disconnected automation display` when at least one member of a due interval is connected.

## Source overview

- `automation.py`: `resolve_interval_targets` intersects saved targets with active IDs; `enforce_interval_exclusivity` and `normalize_interval_conflicts` keep interval ownership exclusive while preserving paused rule records.
- `background.py`: normalizes pre-existing overlapping interval rules before evaluating due events and uses the revised connected-target resolution.
- `gui.py`: saving a quick interval pauses older overlapping timers and reports this in the existing status line.
- `automation_manager_view.py`: resuming a timer asks for confirmation before pausing conflicts. Edit/save is a single atomic `automation.json` update.
- `monitor_labels.py` (new) + `intelligence_view.py`: turn stable IDs into unique, readable labels without changing persisted ID keys.
- `tests/test_automation.py` + `tests/test_c7_monitor_fixes.py`: unit and simulated agent regression tests.

**Validation performed here:** `unittest` on the clean uploaded C7 source after applying this patch: **142 tests passed**; Python compilation and `git diff --check` passed. No physical Windows monitor/lid/Qt UI or Nuitka EXE test was possible in this environment. The C8.1 checkout may have additional tests.
