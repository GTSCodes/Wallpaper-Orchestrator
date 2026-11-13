"""Durable per-display wallpaper restore points, kept separate from rules.

This module does not import Windows COM or Qt so the storage contract can be
regression-tested on any OS. A restore point is an image-path snapshot, not a
backup of the user's physical files or Windows slideshow/theme preferences.
"""

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from wallpaper_orchestrator.automation import automation_path, load_automation, save_automation


def restore_point_path() -> Path:
    return automation_path().with_name("wallpaper-restore-point.json")


def read_restore_point(path: Path | None = None) -> dict[str, str | None] | None:
    """Return saved monitor->wallpaper paths, or None if not yet captured."""
    target = path if path is not None else restore_point_path()
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read wallpaper restore point: {error}") from error
    monitors = data.get("monitors") if isinstance(data, dict) and data.get("version") == 1 else None
    if (not isinstance(monitors, dict) or not monitors or
            any(not isinstance(mid, str) or not mid or
                not (path is None or isinstance(path, str) and bool(path))
                for mid, path in monitors.items())):
        raise ValueError("Invalid wallpaper restore point; original file preserved")
    return monitors


def save_restore_point(monitors: dict[str, str | Path | None], *,
                       path: Path | None = None, overwrite: bool = False) -> bool:
    """Write a complete restore point; never replace a baseline implicitly.

    Hard-link publication makes initial creation atomic even when the GUI and
    background agent start together. Only explicit user action may overwrite.
    """
    target = path if path is not None else restore_point_path()
    normalized = {mid: str(value) if value is not None else None
                  for mid, value in monitors.items()}
    if (not normalized or any(not isinstance(mid, str) or not mid or
                              value == "" for mid, value in normalized.items())):
        raise ValueError("A restore point needs valid monitor IDs and image paths")
    if target.exists():
        read_restore_point(target)  # Do not silently overwrite damaged state.
        if not overwrite:
            return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps({
            "version": 1,
            "captured_utc": datetime.now(timezone.utc).isoformat(),
            "monitors": normalized,
        }, indent=2), encoding="utf-8")
        if overwrite:
            temp.replace(target)
            return True
        try:
            os.link(temp, target)  # Raises if another process already captured.
            return True
        except FileExistsError:
            read_restore_point(target)
            return False
    finally:
        temp.unlink(missing_ok=True)


def capture_wallpapers(discover, get_wallpaper, *, overwrite: bool = False,
                       path: Path | None = None) -> bool:
    """Capture every connected display before the first orchestrated change."""
    target = path if path is not None else restore_point_path()
    if target.exists() and not overwrite:
        read_restore_point(target)
        return False
    monitors = {monitor.device_id: get_wallpaper(monitor.device_id)
                for monitor in discover()}
    return save_restore_point(monitors, path=target, overwrite=overwrite)


def pause_for_restore() -> None:
    """Pause all timers and stop the independent agent before restoration."""
    from wallpaper_orchestrator.background import set_background_enabled, stop_agent
    config = load_automation()
    if not config.paused:
        save_automation(replace(config, paused=True))
    if config.background_enabled:
        set_background_enabled(False)  # Also removes the Windows Run entry.
    if not stop_agent():
        raise RuntimeError("Background agent did not stop; restoration was cancelled")


def restore_wallpapers(monitors: dict[str, str | None], active_ids, apply_wallpaper):
    """Attempt each connected display and report missing/offline wallpapers.

    Caller must call pause_for_restore() first. Never delete or move files.
    Returns (restored ids, skipped messages).
    """
    active = set(active_ids)
    restored: list[str] = []
    skipped: list[str] = []
    for monitor_id, image in monitors.items():
        if monitor_id not in active:
            skipped.append(f"{monitor_id}: display disconnected")
        elif not image:
            skipped.append(f"{monitor_id}: previous wallpaper was blank")
        elif not Path(image).is_file():
            skipped.append(f"{monitor_id}: original wallpaper file unavailable")
        else:
            try:
                apply_wallpaper(monitor_id, Path(image))
            except Exception as error:
                skipped.append(f"{monitor_id}: {error}")
            else:
                restored.append(monitor_id)
    return restored, skipped
