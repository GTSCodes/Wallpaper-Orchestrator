"""Shared wallpaper planning and application logic."""

from pathlib import Path

from wallpaper_orchestrator.config import load_config
from wallpaper_orchestrator.library import load_wallpapers
from wallpaper_orchestrator.models import Monitor
from wallpaper_orchestrator.selector import choose_for_monitors
from wallpaper_orchestrator.state import load_monitor_mapping
from wallpaper_orchestrator.platforms.windows import (
    discover_monitors,
    get_monitor_wallpaper,
    set_monitor_wallpaper,
)


def create_plan(folder: Path):
    """Choose wallpapers for all active monitors."""

    detected = discover_monitors()

    if not detected:
        raise ValueError("No active monitors detected.")

    mapping = load_monitor_mapping()

    roles_by_id = {
        device_id: role
        for role, device_id in mapping.items()
    }

    if any(
        monitor.device_id not in roles_by_id
        for monitor in detected
    ):
        raise ValueError(
            "An active monitor is not configured. "
            "Run monitor setup first."
        )

    monitors = [
        Monitor(
            role=roles_by_id[monitor.device_id],
            orientation=monitor.orientation,
        )
        for monitor in detected
    ]

    wallpapers = [
        wallpaper
        for wallpaper in load_wallpapers(folder)
        if wallpaper.path.suffix.lower()
        in {".jpg", ".jpeg", ".png", ".bmp"}
        and wallpaper.path.is_file()
    ]

    limits = load_config().get("theme_limits", {})

    if not isinstance(limits, dict) or any(
        not isinstance(theme, str)
        or type(limit) is not int
        or limit < 0
        for theme, limit in limits.items()
    ):
        raise ValueError("Invalid theme_limits configuration.")

    assignments = choose_for_monitors(
        monitors,
        wallpapers,
        theme_limits=limits,
    )

    device_ids = {
        role: device_id
        for device_id, role in roles_by_id.items()
        if device_id in {
            monitor.device_id for monitor in detected
        }
    }

    return assignments, device_ids


def apply_plan(assignments, device_ids):
    """Apply a complete plan, attempting rollback on failure."""

    previous = {}

    # Read all existing wallpapers before changing anything.
    for role in assignments:
        device_id = device_ids[role]
        previous[device_id] = get_monitor_wallpaper(device_id)

    changed = []

    try:
        for role, wallpaper in assignments.items():
            device_id = device_ids[role]

            # Include the current monitor in rollback even if
            # Windows reports an error after partially applying it.
            changed.append(device_id)

            set_monitor_wallpaper(
                device_id,
                wallpaper.path,
            )

    except Exception as error:
        failures = []

        for device_id in reversed(changed):
            old = previous[device_id]

            if old is None:
                failures.append(
                    f"{device_id}: no previous wallpaper path"
                )
                continue

            try:
                set_monitor_wallpaper(device_id, old)
            except Exception as restore_error:
                failures.append(str(restore_error))

        details = (
            "Rollback completed."
            if not failures
            else "Rollback incomplete:\n" + "\n".join(failures)
        )

        raise RuntimeError(
            f"Wallpaper application failed: {error}\n{details}"
        ) from error