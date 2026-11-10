"""Wallpaper selection/application shared by the CLI and GUI."""

from collections import Counter
from collections.abc import Collection
from pathlib import Path
from typing import Mapping
import random
import re

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


SUPPORTED = {".jpg", ".jpeg", ".png", ".bmp"}


def _theme_limits() -> dict[str, int]:
    limits = load_config().get("theme_limits", {})
    if not isinstance(limits, dict) or any(
        not isinstance(theme, str)
        or type(limit) is not int
        or limit < 0
        for theme, limit in limits.items()
    ):
        raise ValueError("Invalid theme_limits configuration.")
    return limits


def create_plan(folder: Path):
    """Original CLI-compatible plan: change all mapped active monitors."""
    detected = discover_monitors()
    if not detected:
        raise ValueError("No active monitors detected.")

    mapping = load_monitor_mapping()
    roles_by_id = {device_id: role for role, device_id in mapping.items()}
    if any(m.device_id not in roles_by_id for m in detected):
        raise ValueError("An active monitor has no name. Run monitor setup first.")

    monitors = [
        Monitor(role=roles_by_id[m.device_id], orientation=m.orientation)
        for m in detected
    ]
    wallpapers = [
        w for w in load_wallpapers(folder)
        if w.path.suffix.lower() in SUPPORTED and w.path.is_file()
    ]
    assignments = choose_for_monitors(
        monitors, wallpapers, theme_limits=_theme_limits()
    )
    return assignments, {
        roles_by_id[m.device_id]: m.device_id for m in detected
    }


def _path_key(path: Path) -> str:
    """Compare Windows paths without relying on original case/spelling."""
    return str(path.resolve()).casefold()


def _natural_path_key(path: Path, root: Path):
    """Sort folder entries like File Explorer (image2 before image10)."""
    relative = path.relative_to(root).as_posix().casefold()
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part)
        for part in re.split(r"(\d+)", relative)
    )


def _order_candidates(candidates, folder: Path, profile: Mapping, device_id: str):
    """Prioritize random, next, or previous images without changing rules."""
    order = profile.get("order", "random")
    if order == "random":
        random.shuffle(candidates)
        return candidates
    if order not in {"next", "previous"}:
        raise ValueError("Unknown wallpaper order. Choose Random, Next, or Previous.")

    candidates.sort(key=lambda wallpaper: _natural_path_key(wallpaper.path, folder))
    position_by_path = {
        _path_key(wallpaper.path): index
        for index, wallpaper in enumerate(candidates)
    }
    # Follow the actual desktop when possible, falling back to saved history.
    current = get_monitor_wallpaper(device_id)
    saved = profile.get("last_image", "")
    current_key = _path_key(Path(current)) if current else None
    saved_key = _path_key(Path(saved)) if saved else None
    last_index = position_by_path.get(current_key)
    if last_index is None:
        last_index = position_by_path.get(saved_key)
    step = 1 if order == "next" else -1
    start = (last_index + step) % len(candidates) if last_index is not None else (
        0 if step == 1 else len(candidates) - 1
    )
    return [candidates[(start + offset * step) % len(candidates)]
            for offset in range(len(candidates))]


def create_profile_plan(
    library_folder: Path | None,
    profiles: Mapping[str, dict],
    *,
    selected_device_id: str | None = None,
    selected_device_ids: Collection[str] | None = None,
):
    """Plan a shuffle while preserving all monitors that are not targeted.

    A fixed monitor is never a shuffle target. Non-target monitors contribute
    their *current* wallpaper tags to theme limits, when metadata is known.

    Returned keys are device IDs, so a friendly-name change does not affect
    selections. This is separate from the legacy CLI's role-keyed `create_plan`.
    """
    detected = discover_monitors()
    if not detected:
        raise ValueError("No active monitors detected.")

    if selected_device_id is not None and selected_device_ids is not None:
        raise ValueError("Choose one selection method at a time.")

    active = {m.device_id: m for m in detected}
    explicit_ids = (
        set(selected_device_ids) if selected_device_ids is not None
        else {selected_device_id} if selected_device_id is not None
        else None
    )
    if explicit_ids is not None and not explicit_ids.issubset(active):
        raise ValueError("A selected monitor is no longer active.")

    # Only random-folder monitors can be shuffled.
    targets = []
    sources = {}
    for m in detected:
        profile = profiles.get(m.device_id, {})
        mode = profile.get("mode", "folder")
        if mode not in {"fixed", "folder"}:
            raise ValueError("Unknown wallpaper source mode.")
        if explicit_ids is not None and m.device_id not in explicit_ids:
            continue
        if mode == "fixed":
            if explicit_ids is not None:
                raise ValueError(
                    "A selected monitor uses a fixed image. Select only "
                    "monitors set to shuffle from a folder."
                )
            continue
        if explicit_ids is None:
            if profile.get("shuffle_all", True) is not True:
                continue

        raw_folder = profile.get("folder", "") or (
            str(library_folder) if library_folder is not None else ""
        )
        if not raw_folder:
            raise ValueError("Choose a wallpaper library or monitor folder first.")
        folder = Path(raw_folder).expanduser()
        if not folder.is_dir():
            raise ValueError(f"Wallpaper folder not found: {folder}")
        targets.append(m)
        sources[m.device_id] = folder

    if not targets:
        return {}, {}

    limits = _theme_limits()

    # Index the shared library and each monitor's custom source folder.
    roots = set(sources.values())
    if library_folder is not None and library_folder.is_dir():
        roots.add(library_folder)
    indexed = {}
    by_root = {}
    for root in roots:
        images = [
            w for w in load_wallpapers(root)
            if w.path.suffix.lower() in SUPPORTED and w.path.is_file()
        ]
        by_root[root] = images
        for w in images:
            indexed[_path_key(w.path)] = w

    # Pinned, fixed, or excluded monitors still consume theme capacity.
    counts: Counter[str] = Counter()
    used_images = set()
    target_ids = {m.device_id for m in targets}
    for m in detected:
        if m.device_id in target_ids:
            continue
        current = get_monitor_wallpaper(m.device_id)
        if not current:
            continue
        key = _path_key(Path(current))
        used_images.add(key)
        existing = indexed.get(key)
        if existing is None:
            if limits:
                raise ValueError(
                    "Cannot verify global theme limits: a monitor that will stay "
                    "unchanged displays an image outside the indexed library. "
                    "Include its folder in the library, or set its wallpaper "
                    "to a library image before shuffling."
                )
            continue
        counts.update(existing.themes)

    if any(counts[t] > limit for t, limit in limits.items()):
        raise ValueError("Existing unchanged wallpapers already exceed a theme limit.")

    candidate_sets = {}
    for m in targets:
        candidates = [
            w for w in by_root[sources[m.device_id]]
            if w.orientation == m.orientation
        ]
        if not candidates:
            raise ValueError(
                f"No {m.orientation} wallpapers in {sources[m.device_id]}"
            )
        candidate_sets[m.device_id] = _order_candidates(
            candidates, sources[m.device_id], profiles.get(m.device_id, {}),
            m.device_id,
        )

    # Check more constrained monitors first, then backtrack if needed.
    targets.sort(key=lambda m: len(candidate_sets[m.device_id]))
    assignments = {}

    def search(index: int) -> bool:
        if index == len(targets):
            return True
        m = targets[index]
        for w in candidate_sets[m.device_id]:
            key = _path_key(w.path)
            if key in used_images:
                continue
            if any(counts[theme] >= limits[theme]
                   for theme in w.themes if theme in limits):
                continue
            assignments[m.device_id] = w
            used_images.add(key)
            counts.update(w.themes)
            if search(index + 1):
                return True
            del assignments[m.device_id]
            used_images.remove(key)
            for theme in w.themes:
                counts[theme] -= 1
        return False

    if not search(0):
        raise ValueError(
            "No wallpaper combination satisfies the monitor sources and theme rules."
        )

    # `apply_plan` accepts any assignment key paired with its device ID.
    return assignments, {device_id: device_id for device_id in assignments}


def apply_plan(assignments, device_ids):
    """Apply a plan and attempt to restore earlier wallpapers if it fails."""
    previous = {}
    for key in assignments:
        device_id = device_ids[key]
        previous[device_id] = get_monitor_wallpaper(device_id)

    changed = []
    try:
        for key, wallpaper in assignments.items():
            device_id = device_ids[key]
            changed.append(device_id)  # Include partially-applied updates.
            set_monitor_wallpaper(device_id, wallpaper.path)
    except Exception as error:
        failures = []
        for device_id in reversed(changed):
            old = previous[device_id]
            if old is None:
                failures.append("No previous wallpaper path was available.")
                continue
            try:
                set_monitor_wallpaper(device_id, old)
            except Exception as restore_error:
                failures.append(str(restore_error))
        details = (
            "Rollback completed." if not failures
            else "Rollback incomplete: " + "; ".join(failures)
        )
        raise RuntimeError(
            f"Wallpaper application failed: {error}. {details}"
        ) from error
