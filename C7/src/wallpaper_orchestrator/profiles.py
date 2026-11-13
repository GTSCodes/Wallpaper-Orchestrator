"""Persistent per-monitor wallpaper source preferences.

Profiles are keyed by Windows monitor device ID, never display enumeration order.
They are stored separately from the GUI's QSettings (global library location).
"""

import json
import os
from pathlib import Path
from uuid import uuid4


def profiles_path() -> Path:
    """Return a user-specific, writable settings path."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "WallpaperOrchestrator" / "profiles.json"


def default_profile() -> dict:
    """Defaults for a newly detected monitor."""
    return {
        "mode": "folder",  # fixed, folder, or collections
        "image": "",
        "folder": "",  # Empty means use the shared wallpaper library.
        "order": "random",  # random, next, or previous (per monitor).
        "last_image": "",  # Last successfully applied image for folder navigation.
        "shuffle_all": True,
    }


def load_profiles() -> dict[str, dict]:
    """Load saved preferences; report corruption instead of silently erasing it."""
    path = profiles_path()
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read monitor preferences: {error}") from error

    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported monitor preferences format.")

    profiles = data.get("monitors")
    if not isinstance(profiles, dict):
        raise ValueError("Monitor preferences must contain a monitors object.")

    result = {}
    for device_id, settings in profiles.items():
        if not isinstance(device_id, str) or not isinstance(settings, dict):
            raise ValueError("Invalid monitor preferences entry.")
        profile = default_profile()
        profile.update(settings)
        if profile["mode"] not in {"fixed", "folder", "collections"}:
            raise ValueError("Unknown wallpaper source mode in preferences.")
        if any(not isinstance(profile[k], str)
               for k in ("image", "folder", "last_image")):
            raise ValueError("Invalid image or folder path in preferences.")
        if not isinstance(profile["order"], str) or profile["order"] not in {
            "random", "next", "previous"
        }:
            raise ValueError("Invalid wallpaper order in preferences.")
        if type(profile["shuffle_all"]) is not bool:
            raise ValueError("Invalid Shuffle All preference.")
        result[device_id] = profile
    return result


def save_profiles(profiles: dict[str, dict]) -> None:
    """Save preferences atomically without touching wallpaper image files."""
    path = profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps({"version": 1, "monitors": profiles}, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
