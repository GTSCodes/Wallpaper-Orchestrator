# Wallpaper Orchestrator — C3.3 consolidated source

This release reconciles the modules in the user's 2026-11-10 source upload.
The previous `catalog.py` was the early B1 version, while the indexer and
Library UI expected later B2/B3 APIs. On the uploaded source, 17 of 88 tests
failed; after restoring the missing methods, all 88 passed. An additional
contract test brings this release to **89 passing tests**.

## Installation and scope

1. Commit/back up current repository edits. Do not overwrite user-specific
   `%LOCALAPPDATA%\WallpaperOrchestrator` data with anything from this ZIP.
2. Extract this ZIP at the **repository root**, so that the files end up at
   `src/wallpaper_orchestrator/...` and `tests/...` (not nested in `src`).
3. Restart the GUI and the independent Pythonw background agent. The old
   agent continues running previously loaded Python code until restarted.
4. Run the tests and check agent status:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -q
python -m wallpaper_orchestrator.background --status
```

This is the full **Python source and tests baseline**, not a standalone
Windows installer. It intentionally does not replace the existing project's
`pyproject.toml`, `config.json`, Windows shortcuts, virtual environment, or
personal wallpaper folders.

## Restored catalogue contract

- `get_folder_link()` resolves a stable link ID.
- `find_image_by_path()` reuses indexed metadata.
- `list_folder_images()` finds per-folder associations.
- `apply_folder_scan()` saves a complete scan as one SQLite transaction.
- `rename_tag()` retains all image–tag relationships.
- `delete_tag()` removes the label and relationships but no images.

The existing `SCHEMA_VERSION = 1` is unchanged. No database migration or
file movement is performed by the update. Folder scanning only changes
SQLite metadata and associations. The new `tests/test_contract.py` prevents
future releases from silently omitting these methods.

## Libraries and separation of responsibilities

- Standard-library `sqlite3`: relational catalogue and transactional scans.
- `pathlib`: normalised references to existing files and directories.
- `Pillow`: reads image dimensions/EXIF for indexing, not image modification.
- `PySide6`: GUI, Library previews and schedule dialogs.
- `ctypes`/Windows COM: monitor discovery and wallpaper application in the
  existing Windows platform adapter and background process.

These modules remain independent: the catalogue never applies wallpapers,
and the scheduler asks the engine to select/apply images using profiles.

### Environment constraints

Python syntax checks and 89 unit tests passed in the Linux development
environment; actual Windows COM and Pythonw startup require validation on
the user's Windows system. Existing DB and JSON preferences are not bundled.
