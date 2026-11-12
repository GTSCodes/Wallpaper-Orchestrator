# Wallpaper Orchestrator — Monitor UI Redesign

This bundle contains replacement source files for the **Windows development repository**:

- `gui.py` — reorganised PySide6 monitor-first interface.
- `engine.py` — preserves the existing `create_plan` and `apply_plan` interfaces, and adds `create_profile_plan` to respect fixed/excluded monitors, per-monitor folders and existing theme limits.
- `profiles.py` — **new** module that saves per-monitor preferences to `%LOCALAPPDATA%\WallpaperOrchestrator\profiles.json` using monitor device IDs.

## Install into the project

1. Commit or back up your current working code first.
2. Copy all three `.py` files to `src/wallpaper_orchestrator/`, replacing `gui.py` and `engine.py` there.
3. Keep your existing `cli.py`, `selector.py`, `config.py`, `library.py`, `state.py` and `platforms/windows.py` files. No other Python files need replacement.
4. Run from the project root:

   ```powershell
   .\.venv\Scripts\python.exe -m wallpaper_orchestrator.gui
   ```

5. Your previous *main wallpaper library* saved through QSettings is reused. Monitor-specific preferences are new and initially default to random images from that main library.

## New meaning of the controls

1. Click a monitor rectangle to edit **only that monitor**.
2. **One specific wallpaper:** Choose an image. **Apply This Wallpaper** applies it to that monitor. Shuffle All **does not** change that monitor.
3. **Random images from a folder:** Choose a custom folder or **Use Main Library**. **Shuffle This Monitor** changes only the selected monitor, while respecting the global theme limits against other monitors' current wallpapers.
4. **Include this monitor when I click Shuffle All:** Available for random-folder mode. Uncheck to leave this monitor unchanged on Shuffle All.
5. **Shuffle All Participating Monitors:** Chooses a compatible combination for every enabled random-folder monitor. Fixed/excluded monitors remain unchanged, and their current themes consume global theme-limit capacity.

There is no longer a misleading *Coordinated* source mode: theme limits are global rules loaded from your existing `config.json`.

### Limitations to be aware of

- To apply global theme limits faithfully, an unchanged monitor's current wallpaper must be found in the indexed library or one of the configured monitor folders. If not, the engine stops with an explanation instead of guessing that it has no tags. Add that wallpaper's folder to your library or choose a library image. When no theme limits are configured, unknown unchanged images are permitted.
- Choosing a specific wallpaper outside the indexed library is allowed, but later constrained shuffles may require it to be indexed in order to count its tags.
- Selecting a folder scans it when you shuffle; very large collections may briefly block the GUI. Background indexing is a later milestone.
- No tag editor or folder watcher yet. Existing `folder_themes`, `image_themes` and `theme_limits` still work as before.
- This update is for the development environment. An existing desktop shortcut using `pythonw.exe` will automatically use it if it points to the same working project. Packaged `.exe` copies will **not** update until rebuilt.

## Minimal manual verification

- Open the GUI, select portrait, and choose **One specific wallpaper**. Apply one file. Confirm landscape stays unchanged.
- Select landscape, choose **Random images from a folder**, set its folder and check participation. Click **Shuffle This Monitor**: only landscape changes.
- Click **Shuffle All Participating Monitors**: fixed portrait stays unchanged; landscape changes subject to your theme limits.
- Close and reopen the GUI: verify each monitor's source, folder and participation preference survived.

These are practical checks on your real monitors; no test files are included.
