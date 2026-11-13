
"""Persistent, machine-specific application state."""

import json
import os
from pathlib import Path


def get_mapping_path() -> Path:
    """Return the location of the local monitor mapping."""

    appdata = os.environ["LOCALAPPDATA"]

    return (
        Path(appdata)
        / "WallpaperOrchestrator"
        / "monitors.json"
    )


def load_monitor_mapping() -> dict[str, str]:
    """Load previously identified monitor roles."""

    path = get_mapping_path()

    if not path.exists():
        return {}

    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, dict) or not all(
        isinstance(role, str) and isinstance(device_id, str)
        for role, device_id in data.items()
    ):
        raise ValueError("Invalid monitor mapping file.")

    return data


def save_monitor_mapping(mapping: dict[str, str]) -> None:
    """Save monitor roles to the local application directory."""

    path = get_mapping_path()

    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_suffix(".tmp")

    temporary_path.write_text(
        json.dumps(mapping, indent=4),
        encoding="utf-8",
    )

    temporary_path.replace(path)
