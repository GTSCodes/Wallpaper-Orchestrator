# Wallpaper Orchestrator — Per-display dropdown follow-up (C8.1)

This patch is **on top of** `WPO_C8_1_monitor_hotfix.patch` (the monitor-automation patch you already applied). It fixes a missing change in that patch: the actual **Intelligent wallpaper selection → Per-display rules** dropdown was never switched to unique labels. The first hotfix had only updated the monitor-group editor and the Automation Centre.

## Contents

- `src/wallpaper_orchestrator/intelligence_view.py` — changes the per-display dropdown to show the role, resolution, device model/UID and stable short identifier. The full Windows monitor ID is still used as the saved configuration key. A tooltip shows the complete label if the dropdown text is cut off.
- `tests/test_monitor_dialog_labels.py` — a regression test that checks the **Per-display rules dropdown itself** calls the unique-label helper. Previous tests only tested that the helper could generate unique labels, so they missed the unmodified dropdown.

The patch contains **no personal configuration, wallpapers, images, SQLite databases or compiled executables**.

## Apply to your C8.1 repository (PowerShell)

1. Back up or commit any current source changes. Leave the existing monitor hotfix applied; do **not** reapply the first hotfix.
2. Extract this ZIP into the repository root (alongside `src` and `tests`), leaving the `WPO_per_display_followup` folder intact.
3. Run:

```powershell
git apply --check .\WPO_per_display_followup\WPO_per_display_labels_followup.patch
git apply .\WPO_per_display_followup\WPO_per_display_labels_followup.patch
python -m unittest discover -s tests -q
```

If `git apply --check` fails, stop and share the output. Do not force or overwrite the source with an old version.

4. Quit any running GUI from the tray using **Exit GUI** (not just window X), then reopen from the updated source:

```powershell
python -m wallpaper_orchestrator.gui
```

5. Go to Displays → Intelligent wallpaper selection → Per-display rules. The choices should be distinctive, e.g. `landscape — 1920×1080 — HKC2794 / UID... [04415d]` instead of only `landscape`. Save and reopen to check separate rules remain attached to separate monitors.

## Verify the *background agent* is updated, separately

The wallpaper GUI and independent background agent are different processes. Updating the `.py` files **does not reload an agent already running**, and rebuilding source **does not modify an older packaged `.exe`**. A stale agent can continue reporting `Waiting for a disconnected automation display` even when the updated source supports partial monitor connectivity.

1. In Schedules → Manage all automations, uncheck **Keep all enabled automations running after the app window closes**, click **Apply background setting**.
2. Wait for the agent to stop. Confirm with:

```powershell
python -m wallpaper_orchestrator.background --status
```

Expect `Background enabled: False` and the agent no longer `Running`. If still running, inspect the exact executable/command line before terminating anything:

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('pythonw.exe','WallpaperOrchestrator.exe') -and $_.CommandLine -match 'wallpaper_orchestrator|WallpaperOrchestrator' } | Select-Object ProcessId,Name,CommandLine
```

3. While **testing from source**, re-enable background mode from the *newly opened source GUI*. Confirm status now shows `Agent: Running`. Check after the next interval whether only the connected external monitors change when the laptop display is disconnected. The final message from an old run may remain visible until a new update is due.
4. For the packaged executable, **rebuild it** after patches using the C8.1 build script (rename the old `dist\WallpaperOrchestrator` folder first), and start the new build. Make sure a shortcut or Windows startup setting isn't launching an old `WallpaperOrchestrator.exe`.

## Confirm the other two changes

- **Disconnected monitor:** Choose one 1-minute interval for three monitors. Keep Windows awake while closing the laptop display. After an interval, two connected monitors should change; reopening the lid should bring the laptop monitor back on a subsequent interval. The Windows `IDesktopWallpaper` API may still report three *associated* monitors while one is disconnected; failing geometry lookup for that third monitor alone is not a test failure.
- **Interval conflicts:** Create a portrait-only interval, then a portrait + landscape interval. The first should become Paused; in Automation Centre try to resume it and check the confirmation warning. The screenshot of one Interval and one Seasonal rule does not yet test this overlap scenario.

## Validation and limitations

- Applied cleanly on top of the first monitor hotfix in the uploaded C7 source.
- **143 headless tests passed** against that source with this patch (the user's C8.1 tree may have additional tests). Python source compiled successfully.
- PySide6 and the Windows desktop APIs are unavailable in the verification environment, so dropdown rendering, agent process identity, physical lid close and packaged `.exe` need confirmation on your computer.
- This follow-up only changes dropdown labelling and its test. It does **not** replace the existing partial-targeting and conflict-resolution logic from the first patch.
