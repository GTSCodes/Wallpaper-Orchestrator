"""Pure decisions for offering the C8.2 first-run wizard.

Never mark an upgrading user as 'new' just because the C8.2 flag is absent.
"""

import os
from pathlib import Path


USER_DATA_NAMES = (
    "catalog.sqlite3", "profiles.json", "automation.json", "schedules.json",
    "config.json", "calendar-rules.json", "calendar_rules.json",
    "intelligence.json", "seasonal-state.json",
)


def app_data_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".config") / "WallpaperOrchestrator"


def should_offer_setup(settings, *, app_data: Path | None = None) -> bool:
    """Offer only to users with no remembered settings or existing app data."""
    if settings.value("setup/finished", False, type=bool):
        return False
    if settings.allKeys():
        return False
    folder = app_data if app_data is not None else app_data_directory()
    return not any((folder / name).exists() for name in USER_DATA_NAMES)
