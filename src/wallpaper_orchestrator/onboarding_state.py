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


def default_background_for_setup(config, *, saved_preference_exists: bool) -> bool:
    """New installations opt into background mode; existing choices win.

    A restore action persists background_enabled=False and paused=True; when
    setup is reopened, never treat that deliberate pause as a fresh install.
    """
    return config.background_enabled if saved_preference_exists else True
