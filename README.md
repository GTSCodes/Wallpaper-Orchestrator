# Wallpaper Orchestrator

A Windows-first, open-source wallpaper manager for multi-monitor desktops. It detects active screens (including portrait displays), lets you choose wallpapers for individual monitors, and can select a **coordinated set of wallpapers** that respects theme limits.

> **Project status:** Early development / alpha. C7 includes the Library, intelligent selection, schedules, automation manager and tray companion. C8.1 adds a Windows standalone build workflow; an installer and onboarding are still planned.

## C8.1 standalone Windows build

To create a portable executable that users can run without installing Python,
see [C8_1_README.md](C8_1_README.md). On Windows, the source build command is
`./scripts/build_windows.ps1`; alternatively use the **C8 Windows portable
build** GitHub Actions workflow. The resulting `dist/WallpaperOrchestrator`
directory must be distributed *in full*, including the runtime dependencies.

## What works today

- Discover active Windows monitors, their resolutions, orientations and positions.
- Give monitors friendly names such as `portrait` and `landscape`.
- Scan a wallpaper library recursively for supported images and inspect their orientation.
- Infer themes from folder names, with optional explicit tag overrides in configuration.
- Suggest a random portrait or landscape wallpaper.
- Change one monitor's wallpaper or shuffle one monitor.
- Plan and apply a coordinated shuffle across **all active monitors**, respecting global theme-count limits.
- Display monitors as clickable, correctly proportioned rectangles in a desktop GUI.
- Browse for a specific image or wallpaper library and apply/shuffle from the GUI.
- Remember the main wallpaper-library folder between GUI launches (in versions containing the `QSettings` change).

## Requirements

- **Windows 10/11** for monitor detection and wallpaper application (other operating systems are planned).
- **Python 3.12 or later** for development.
- A wallpaper folder containing images (`.jpg`, `.jpeg`, `.png`, `.bmp` are supported for applying wallpapers).

## Installation from source (Windows / PowerShell)

Open PowerShell in the repository directory:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"
```

If you use another compatible Python version, adjust the first command. The `-e .` installation means edits to the source code are used without reinstalling the project each time.

Confirm that the command-line program is installed:

```powershell
wallpaper-orchestrator --help
```

If `wallpaper-orchestrator` is not on your shell's PATH, use:

```powershell
.\.venv\Scripts\wallpaper-orchestrator.exe --help
```

## First-time setup

### 1. Identify connected monitors

```powershell
wallpaper-orchestrator monitors
```

This prints active monitors, their pixel dimensions, orientation and position. A closed laptop screen may be excluded when disabled in Windows. Some Windows systems report disconnected monitor entries; the application skips entries whose geometry cannot be retrieved.

### 2. Give each monitor a friendly name

```powershell
wallpaper-orchestrator setup
```

Enter a distinct name for each detected monitor, for example `portrait` and `landscape`. These names are mapped to Windows device IDs and saved in:

```text
%LOCALAPPDATA%\WallpaperOrchestrator\monitors.json
```

Run `setup` again if your display hardware or mapping changes.

### 3. Identify your wallpaper library

Choose one parent folder containing your wallpaper images and optional subfolders, such as:

```text
Pictures\Wallpapers\
├── Catholic Art\
├── Miles Morales\
└── Laptop Wallpapers\
```

**This layout is only an example, not a requirement.** Scanning is recursive. Folder names are currently used as fallback theme labels; explicit theme mappings can override them. More flexible tagging and theme editing are on the roadmap.

## Command reference

The examples below assume `wallpaper-orchestrator` is on your PATH. Always quote Windows paths that may contain spaces. Replace the sample library path with your own.

### `--help` and `--version`

```powershell
wallpaper-orchestrator --help
wallpaper-orchestrator --version
wallpaper-orchestrator shuffle --help
```

Show available commands, the installed application version, or help for a subcommand.

### `monitors` — list active screens

```powershell
wallpaper-orchestrator monitors
```

Displays each active monitor's resolution, orientation and position. Does not change wallpapers.

### `setup` — save monitor names

```powershell
wallpaper-orchestrator setup
```

Prompts for a friendly name for each active display. Saves the mapping for later `set`, `shuffle` and `plan` commands. Does not change wallpapers.

### `scan` — inspect your collection

```powershell
wallpaper-orchestrator scan "C:\Users\YourName\Pictures\Wallpapers"
```

Recursively lists wallpaper images with image dimensions, portrait/landscape classification and inferred folder theme. Does not change wallpapers. Very large or malformed images may produce Pillow warnings/errors.

### `suggest` — pick a wallpaper for an orientation

```powershell
wallpaper-orchestrator suggest "C:\Users\YourName\Pictures\Wallpapers" --orientation portrait
wallpaper-orchestrator suggest "C:\Users\YourName\Pictures\Wallpapers" --orientation landscape
```

Prints a randomly selected orientation-compatible image and its themes. It does **not** apply the image or account for all other monitors' current selections.

### `set` — assign one specific image

**Preview (no changes):**

```powershell
wallpaper-orchestrator set --monitor portrait --image "C:\Users\YourName\Pictures\Wallpapers\portrait.jpg"
```

**Apply:**

```powershell
wallpaper-orchestrator set --monitor portrait --image "C:\Users\YourName\Pictures\Wallpapers\portrait.jpg" --apply
```

`--monitor` is the friendly name saved by `setup`. `--image` must point to an existing supported image. Without `--apply`, the command is a dry run.

### `shuffle --monitor` — randomise one monitor

**Preview:**

```powershell
wallpaper-orchestrator shuffle --monitor landscape --folder "C:\Users\YourName\Pictures\Wallpapers"
```

**Apply:**

```powershell
wallpaper-orchestrator shuffle --monitor landscape --folder "C:\Users\YourName\Pictures\Wallpapers" --apply
```

Selects a random wallpaper compatible with that monitor's orientation. In the current implementation, this individual shuffle does **not** enforce all cross-monitor theme limits against wallpapers already displayed elsewhere.

### `plan` — preview a coordinated multi-monitor selection

```powershell
wallpaper-orchestrator plan --folder "C:\Users\YourName\Pictures\Wallpapers"
```

Selects a compatible wallpaper for every active, configured monitor and prints its image and themes. Reads global `theme_limits` from configuration. **Never changes wallpapers.** Repeating the command can produce different selections.

### `shuffle --all` — shuffle all active monitors together

**Preview (dry run):**

```powershell
wallpaper-orchestrator shuffle --all --folder "C:\Users\YourName\Pictures\Wallpapers"
```

**Apply the coordinated set:**

```powershell
wallpaper-orchestrator shuffle --all --folder "C:\Users\YourName\Pictures\Wallpapers" --apply
```

Uses orientation compatibility and configured theme limits to choose different images for active monitors. Before applying, it attempts to read existing wallpaper paths. If an application step fails, it attempts to restore changed monitors. **Rollback is best-effort, not guaranteed.**

`--all` and `--monitor NAME` are mutually exclusive.

## Desktop GUI

Launch the graphical interface from the development environment:

```powershell
.\.venv\Scripts\python.exe -m wallpaper_orchestrator.gui
```

Or launch it **without opening a console window**:

```powershell
.\.venv\Scripts\pythonw.exe -m wallpaper_orchestrator.gui
```

The GUI currently provides:

1. **Monitor map:** Click a monitor rectangle; rectangles reflect detected dimensions and relative positions.
2. **Wallpaper behaviour:** `Fixed Image`, `Random Folder`, or `Coordinated` (initial implementation; choices are not yet saved per monitor).
3. **Browse / Apply Wallpaper:** Select a specific image and apply it to the selected monitor.
4. **Choose Folder:** Choose the shared library location. In the updated GUI, the last chosen folder is remembered via Qt's `QSettings`.
5. **Shuffle Selected Monitor:** In Fixed Image mode, applies the selected image; in Random Folder mode, chooses a compatible image from the chosen library; in Coordinated mode, triggers the all-monitor shuffle.
6. **Shuffle All:** Creates a coordinated plan and applies it to all active monitors (current GUI implementation does not yet respect persistent per-monitor fixed/paused preferences).

If controls are greyed out, click a monitor rectangle first.

### Desktop shortcut without VS Code

Until packaged releases are reliable, you can create a Windows desktop shortcut to the working Python GUI:

**Shortcut target:**

```text
"C:\Path\To\Wallpaper-Orchestrator\.venv\Scripts\pythonw.exe" -m wallpaper_orchestrator.gui
```

**Start in:**

```text
C:\Path\To\Wallpaper-Orchestrator
```

Replace both paths with your actual project location. This runs without VS Code or PowerShell, but requires the project and `.venv` to remain installed at those paths.

### Packaging note

The project has been tested with PyInstaller, but some Windows `--windowed` builds have failed with a PySide6 `QtCore` DLL import error. A `--console` diagnostic build has worked. **Do not assume a particular packaged `.exe` is reliable without testing it.** A polished installer is a future task.

## Theme configuration

The current program can read a configuration file at:

```text
%LOCALAPPDATA%\WallpaperOrchestrator\config.json
```

Example:

```json
{
  "folder_themes": {
    "Catholic Art": ["Catholic", "Art"],
    "Miles Morales": ["Marvel", "Gaming"],
    "Laptop Wallpapers": ["General"]
  },
  "image_themes": {},
  "theme_limits": {
    "Catholic": 1
  }
}
```

- `folder_themes` maps a library-relative folder name/path to one or more tags.
- `image_themes` permits per-image theme overrides (using paths relative to the library).
- `theme_limits` sets the **maximum number of monitors** that may simultaneously receive wallpapers with a given tag during coordinated planning. For example, `"Catholic": 1` permits at most one Catholic-themed selection across the planned monitors.

Theme labels are user-defined; you do not need to use the examples above. This is currently a manual JSON configuration, not a complete GUI theme editor. Keep a copy before editing manually.

## Current limitations

- Windows-only monitor discovery/application.
- Requires explicit monitor setup/mapping for coordinated operations.
- Individual monitor behaviour preferences are not yet persistent, despite the shared library folder being remembered.
- `shuffle --all` currently plans wallpapers for *all active mapped monitors*; fixed/paused monitor participation and per-monitor source preferences are still planned.
- Folder-based theme inference is a fallback, not a full content-aware image categoriser.
- Wallpaper application is best-effort, including recovery after partial failure.
- Desktop packaging still needs reliability work for PySide6 on Windows.

## Roadmap

- [ ] Dedicated per-monitor settings panels and saved per-monitor preferences.
- [ ] Fixed, random-folder, random-theme and coordinated modes that work together.
- [ ] Theme groups, maximum/minimum limits and fixed monitors counting toward rules.
- [ ] Editable theme tags without strict folder organisation.
- [ ] Wallpaper preview thumbnails and a clearer desktop interface.
- [ ] Scheduling, startup integration, and background/tray mode.
- [ ] Reliable Windows executable/installer, application icon and releases.
- [ ] macOS and Linux platform adapters.

## Development notes

Main modules in `src/wallpaper_orchestrator/`:

| Module | Responsibility |
| --- | --- |
| `cli.py` | Command-line argument parsing and commands |
| `gui.py` | PySide6 desktop interface |
| `engine.py` | Reusable coordinated planning and wallpaper application |
| `library.py` | Scan files and load image metadata |
| `models.py` | Monitor and wallpaper data classes |
| `selector.py` | Random/orientation-compatible and coordinated selection |
| `config.py` | Theme configuration and overrides |
| `state.py` | Saved monitor-name mappings |
| `platforms/windows.py` | Windows display discovery and wallpaper COM calls |

To run existing tests when needed:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The project deliberately keeps testing focused on meaningful behaviour rather than creating large numbers of demonstration tests.

## Contributing

This is an early-stage personal/open-source project. Issues, suggestions and improvements are welcome as the architecture stabilises. Please avoid committing local virtual environments, generated executables, personal wallpaper libraries, or private local configuration files.

## License

The project is intended to use the **MIT License**. Add a `LICENSE` file to the repository if it is not already present so the license terms are explicit.
