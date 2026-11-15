"""Non-destructive, versioned snapshots of per-user application state.

SQLite is backed up with SQLite's online backup API (safe with WAL). JSON and
QSettings registry values are copied; original wallpaper image files are not.
Backups are never used as live configuration and never overwrite restore points.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from uuid import uuid4


_SAVED_JSON = (
    "config.json", "profiles.json", "monitors.json", "automation.json",
    "schedules.json", "calendar_rules.json", "selection-rules.json",
    "smart-collections.json", "seasonal-state.json",
    "wallpaper-restore-point.json", "wallpaper-first-restore-point.json",
)


def data_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".config") / "WallpaperOrchestrator"


def _hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _sqlite_copy(source: Path, target: Path) -> None:
    # Open read-only; do not change journal mode or schema of the user's DB.
    connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    try:
        destination = sqlite3.connect(target)
        try:
            connection.backup(destination, pages=100, sleep=0.1)
            if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("Backup catalogue integrity check failed")
        finally:
            destination.close()
    finally:
        connection.close()


def _registry_snapshot(target: Path) -> bool:
    if sys.platform != "win32":
        return False
    # Qt's native QSettings store is per-user HKCU. No elevation required.
    import winreg
    subkey = r"Software\WallpaperOrchestrator\WallpaperOrchestrator"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ):
            pass
    except FileNotFoundError:
        return False  # Fresh user has no Qt settings yet.
    registry_key = "HKCU\\" + subkey
    completed = subprocess.run([
        "reg.exe", "export", registry_key, str(target), "/y",
    ], capture_output=True, timeout=20, check=False)
    if completed.returncode != 0 or not target.is_file():
        raise OSError("Cannot back up the application's QSettings registry key")
    return True


def create_backup(*, root: Path | None = None,
                  catalog_path: Path | None = None,
                  include_qsettings: bool = True) -> Path | None:
    """Snapshot state into a new immutable-name folder; never mutate source.

    Returns None if no existing state needs backing up (e.g. fresh install).
    Any source or backup I/O failure is propagated so an unsafe upgrade stops.
    """
    directory = Path(root) if root is not None else data_root()
    catalogue = Path(catalog_path) if catalog_path is not None else directory / "catalog.sqlite3"
    settings = [directory / name for name in _SAVED_JSON
                if (directory / name).is_file()] if directory.is_dir() else []
    if not catalogue.is_file() and not settings and not (
        include_qsettings and sys.platform == "win32"
    ):
        return None

    backup_root = directory / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    moment = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"backup-{moment}-{uuid4().hex[:10]}"
    staging = backup_root / ("." + name + ".tmp")
    published = backup_root / name
    staging.mkdir()
    try:
        copied = []
        if catalogue.is_file():
            destination = staging / "catalog.sqlite3"
            _sqlite_copy(catalogue, destination)
            copied.append(destination)
        for source in settings:
            if source.is_symlink() or not source.is_file():
                continue
            destination = staging / source.name
            shutil.copy2(source, destination)
            copied.append(destination)
        registry = staging / "qt-settings.reg"
        if include_qsettings and _registry_snapshot(registry):
            copied.append(registry)
        if not copied:
            shutil.rmtree(staging)
            return None
        manifest = {
            "format": 1,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source": str(directory),
            "files": [{"name": item.name, "sha256": _hash(item),
                       "bytes": item.stat().st_size} for item in copied],
            "note": "Restore manually while the app and background agent are stopped.",
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        staging.rename(published)
        return published
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
