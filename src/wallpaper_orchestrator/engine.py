"""Wallpaper selection/application shared by the CLI and GUI."""

from collections import Counter
from collections.abc import Collection
from pathlib import Path
from typing import Mapping
import random
import re

from wallpaper_orchestrator.config import load_config
from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.library import load_wallpapers
from wallpaper_orchestrator.models import Monitor, Wallpaper
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


def _natural_path_key(path: Path, root: Path | None):
    """Sort folder entries like File Explorer (image2 before image10)."""
    try:
        relative = path.relative_to(root).as_posix().casefold() if root else path.as_posix().casefold()
    except ValueError:
        relative = path.as_posix().casefold()
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part)
        for part in re.split(r"(\d+)", relative)
    )


def _order_candidates(candidates, folder: Path | None, profile: Mapping, device_id: str):
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


def _catalog_wallpapers(catalog: WallpaperCatalog):
    """Convert indexed images into existing selection-engine Wallpaper models.

    Both collection names and explicit tags become temporary theme names for
    backwards compatibility with JSON `theme_limits`. Image relationships
    remain separately stored by ID in SQLite.
    """
    image_records = {}
    collection_records = {}
    for collection in catalog.list_collections():
        records = catalog.list_collection_images(collection.id)
        collection_records[collection.id] = records
        for record in records:
            image_records[record.id] = record

    by_image_id = {}
    for image_id, record in image_records.items():
        if (not record.available or not record.path.is_file()
                or record.path.suffix.lower() not in SUPPORTED):
            continue
        themes = {
            collection.name for collection in catalog.list_image_collections(image_id)
        }
        themes.update(tag.name for tag in catalog.list_image_tags(image_id))
        by_image_id[image_id] = Wallpaper(
            path=record.path,
            orientation=record.orientation,
            themes=frozenset(themes),
        )
    by_collection = {
        collection_id: [by_image_id[r.id] for r in records if r.id in by_image_id]
        for collection_id, records in collection_records.items()
    }
    return by_image_id, by_collection


def _selection_failure_message(
    targets, candidate_sets, used_images, limits, counts, theme_owners
) -> str:
    """Explain a proven blocker without guessing at the user's configuration."""
    unused = {
        m.device_id: [
            image for image in candidate_sets[m.device_id]
            if _path_key(image.path) not in used_images
        ]
        for m in targets
    }
    for monitor in targets:
        if not unused[monitor.device_id]:
            return (
                f"The {monitor.orientation} display has matching images, but "
                "all of them are already in use on unchanged displays. "
                "Assign additional images or update those displays together."
            )

    for theme, limit in sorted(limits.items()):
        # Every display in this set *must* use this theme: none has an
        # alternative candidate without it. Their combined demand is a
        # lower bound, even when their images overlap.
        required = [
            monitor for monitor in targets
            if all(theme in image.themes for image in unused[monitor.device_id])
        ]
        if len(required) > limit - counts[theme]:
            owners = theme_owners.get(theme, [])
            held_by = (
                f" Currently displayed on: {', '.join(owners)}."
                if owners else ""
            )
            return (
                f"Theme limit reached for '{theme}': maximum {limit} "
                f"display(s), already used on {counts[theme]}, and "
                f"{len(required)} selected display(s) require it."
                f"{held_by} Change an existing wallpaper, select another "
                "collection, or turn off Cross-monitor limits."
            )

    unique_images = {
        _path_key(image.path)
        for choices in unused.values() for image in choices
    }
    if len(unique_images) < len(targets):
        return (
            f"Only {len(unique_images)} distinct matching wallpaper(s) "
            f"are available for {len(targets)} selected displays. Each "
            "display needs a different image. Add images or change "
            "collection subscriptions."
        )

    counts_text = ", ".join(
        f"{monitor.orientation} ({len(unused[monitor.device_id])} images)"
        for monitor in targets
    )
    rule_hint = (
        " The active cross-monitor theme limits may be preventing a "
        "valid combination; try turning them off or changing the rules."
        if limits else " The available images overlap across displays."
    )
    return f"No compatible combination found for {counts_text}.{rule_hint}"


def create_profile_plan(
    library_folder: Path | None,
    profiles: Mapping[str, dict],
    *,
    selected_device_id: str | None = None,
    selected_device_ids: Collection[str] | None = None,
    respect_theme_limits: bool = True,
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

    # Fixed monitors are never shuffle targets. Folder and collection modes
    # both reuse the same coordinated planning/search algorithm.
    targets = []
    sources = {}
    collection_targets = set()
    for m in detected:
        profile = profiles.get(m.device_id, {})
        mode = profile.get("mode", "folder")
        if mode not in {"fixed", "folder", "collections"}:
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

        if mode == "collections":
            targets.append(m)
            collection_targets.add(m.device_id)
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

    # Cross-monitor limits are optional. The monitor's collection membership
    # and image orientation always determine basic eligibility; a legacy
    # config.json rule must never imply a portrait-only collection.
    limits = _theme_limits() if respect_theme_limits else {}

    # Folder-mode targets still use the legacy filesystem reader. Collection
    # targets use SQLite and should not rescan the unrelated main library (or
    # inherit its old folder_themes overrides) on every shuffle.
    roots = set(sources.values())
    if limits and library_folder is not None and library_folder.is_dir():
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

    collection_candidates = {}
    collection_source_details = {}
    if collection_targets:
        with WallpaperCatalog() as catalog:
            indexed_catalog, by_collection = _catalog_wallpapers(catalog)
            for wallpaper in indexed_catalog.values():
                key = _path_key(wallpaper.path)
                # Indexed collection membership and explicit database tags
                # are authoritative for these images. Legacy folder_themes
                # may be stale after the user moves a physical image.
                indexed[key] = wallpaper
            for device_id in collection_targets:
                subscriptions = catalog.list_monitor_collections(device_id)
                if not subscriptions:
                    raise ValueError(
                        "This monitor has no collections assigned. "
                        "Choose its collections in the Displays tab."
                    )
                options = {}
                details = []
                for collection in subscriptions:
                    images = by_collection[collection.id]
                    matching = sum(
                        w.orientation == active[device_id].orientation
                        for w in images
                    )
                    details.append(
                        f"'{collection.name}': {matching} "
                        f"{active[device_id].orientation} image(s), "
                        f"{len(images)} available total"
                    )
                    for wallpaper in images:
                        options[_path_key(wallpaper.path)] = indexed[
                            _path_key(wallpaper.path)
                        ]
                collection_candidates[device_id] = list(options.values())
                collection_source_details[device_id] = details

    # Pinned, fixed, or excluded monitors still consume theme capacity.
    counts: Counter[str] = Counter()
    used_images = set()
    theme_owners = {}
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
        for theme in existing.themes:
            theme_owners.setdefault(theme, []).append(
                getattr(m, "name", None) or f"{m.orientation} display"
            )

    for theme, limit in limits.items():
        if counts[theme] > limit:
            raise ValueError(
                f"Theme limit for '{theme}' is {limit}, but "
                f"{counts[theme]} unchanged display(s) already use it "
                f"({', '.join(theme_owners.get(theme, []))}). "
                "Change an existing wallpaper or turn off Cross-monitor limits."
            )

    candidate_sets = {}
    for m in targets:
        collection_mode = m.device_id in collection_targets
        candidates = (
            collection_candidates[m.device_id] if collection_mode else
            [indexed[_path_key(w.path)] for w in by_root[sources[m.device_id]]]
        )
        candidates = [w for w in candidates if w.orientation == m.orientation]
        if not candidates:
            if collection_mode:
                details = "; ".join(collection_source_details[m.device_id])
                raise ValueError(
                    f"No {m.orientation} wallpapers available in this "
                    f"display's assigned collections. {details}. "
                    "Add an image with a matching orientation and scan "
                    "the collection."
                )
            available = by_root[sources[m.device_id]]
            raise ValueError(
                f"No {m.orientation} wallpapers available in folder "
                f"'{sources[m.device_id]}' ({len(available)} supported "
                "image(s) of other orientations)."
            )
        candidate_sets[m.device_id] = _order_candidates(
            candidates, None if collection_mode else sources[m.device_id],
            profiles.get(m.device_id, {}),
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
            _selection_failure_message(
                targets, candidate_sets, used_images, limits, counts, theme_owners
            )
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
