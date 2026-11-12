# Wallpaper Orchestrator — Automatic Display Refresh

Replace only `src/wallpaper_orchestrator/gui.py` with the `gui.py` in this ZIP.
Leave your existing `engine.py`, `profiles.py`, wallpaper library and monitor
preferences in place.

## New behavior

- The app listens for Windows/Qt screen additions, removals, primary-screen
  changes, and changes to monitor geometry or orientation. It waits briefly
  for the new display configuration to settle before checking connected
  monitors.
- A small, muted **↻** icon next to **Select all** can refresh manually if
  Windows does not send a screen-change notification.
- When a display is removed, the GUI removes its rectangle and deselects it
  without closing the app. Still-connected monitors remain selected.
- When a display is added or moved, the diagram rescales to fit the new
  arrangement. Newly connected displays receive a saved name when a device-ID
  mapping exists, otherwise a temporary `Monitor N` label.
- The settings panel remains in place. When zero or multiple displays are
  selected, its dropdowns and other controls are visibly disabled; the main
  library selector remains available.
- Labels and buttons use the correct singular/plural wording for any number
  of selected displays: `1 display`, `2 displays`, `3 displays`, etc.

## Verification

- Python syntax passed.
- Simulated wallpaper plans for three and four monitors passed previously,
  including unique wallpapers and theme-limit enforcement.
- Monitor topology checks passed for three-to-four addition, removal,
  geometry changes and enumeration-order changes.

The Windows hot-plug event behavior must still be checked on an actual
Windows multi-monitor computer. The manual **↻** button is always available
as a fallback.

Restart your existing desktop shortcut after replacing `gui.py`.
