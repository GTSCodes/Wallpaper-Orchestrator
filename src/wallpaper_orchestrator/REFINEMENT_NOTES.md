# Wallpaper Orchestrator — simpler monitor selection

Replace these project files with the versions in the ZIP:

- `src/wallpaper_orchestrator/gui.py`
- `src/wallpaper_orchestrator/engine.py`

Your existing `profiles.py`, wallpaper library, saved monitor names, and
per-monitor preferences remain unchanged. There is no migration required.

## How the new interface works

1. Click a display rectangle to select it. Click another to add it to the
   selection; click a selected display again to deselect it. **Select all**
   selects every visible display.
2. Selected displays are bright and outlined; others are faded. Individual
   wallpaper preferences appear only when exactly one display is selected.
3. Hover over a selected display and click the picture icon in its corner to
   choose an image. The image is applied straight away and that display is
   remembered as a **Fixed image** display.
4. For a random display, choose **Shuffle from folder**. Use the main library
   or a custom folder for that display. The bottom action becomes **Shuffle
   display** or **Shuffle N displays**, depending on how many are selected.
5. An action is disabled (greyed out) when it cannot run: no selection, no
   usable folder, no valid fixed image, or a multi-selection containing a
   fixed-image display. Hover over the disabled action for a short hint.

The older **Shuffle All Participating Monitors** button and the checkbox
**Include this monitor when I click Shuffle All** are removed. The chosen
selection now determines exactly which displays will change.

## Try it on Windows

From your repository root, after replacing the files:

```powershell
.\.venv\Scripts\python.exe -m wallpaper_orchestrator.gui
```

Your existing desktop shortcut should continue to work if it launches
`pythonw.exe -m wallpaper_orchestrator.gui` from the same virtual environment.

No wallpaper files are moved or renamed by this update. This package has been
syntax-checked; actual UI interaction must be verified on Windows with PySide6.
