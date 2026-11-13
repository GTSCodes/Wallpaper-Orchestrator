"""Wallpaper selection/application shared by the CLI and GUI."""

from collections import Counter
from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from typing import Mapping
import random
import re
import sqlite3
import warnings

from wallpaper_orchestrator.config import load_config
from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.calendar_rules import (
    CalendarRule, active_calendar_rule, load_calendar_rules,
)
from wallpaper_orchestrator.intelligence import SelectionPolicy, load_selection_policy
from wallpaper_orchestrator.recent_history import RecentHistory
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
        # A shuffle should visibly change the wallpaper whenever it can.
        # The old random path could immediately reselect the current image.
        if len(candidates) > 1:
            try:
                current = get_monitor_wallpaper(device_id)
            except Exception:
                # Query failures should not prevent an otherwise valid random
                # shuffle. Saved history still gives a useful best effort.
                current = None
            current = current or profile.get("last_image", "")
            if current and _path_key(candidates[0].path) == _path_key(Path(current)):
                alternative = next((index for index, wallpaper in enumerate(candidates[1:], 1)
                                    if _path_key(wallpaper.path) != _path_key(Path(current))), None)
                if alternative is not None:
                    candidates[0], candidates[alternative] = (
                        candidates[alternative], candidates[0]
                    )
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


def _catalog_memberships(catalog: WallpaperCatalog) -> dict[str, tuple[set[str], set[str]]]:
    """Stable tag/collection IDs keyed by image path, including smart collections."""
    result = {}
    for collection in catalog.list_collections():
        for image in catalog.list_collection_images(collection.id):
            key = _path_key(image.path)
            if key not in result:
                result[key] = ({tag.id for tag in catalog.list_image_tags(image.id)}, set())
            result[key][1].add(collection.id)
    return result


def _rule_match(rule, membership: tuple[set[str], set[str]]) -> bool:
    return rule.matches(*membership)


def _selection_failure_message(
    targets, candidate_sets, used_images, limits, counts, theme_owners,
    *, avoid_duplicates: bool = True,
) -> str:
    """Explain a proven blocker without guessing at the user's configuration."""
    unused = {
        m.device_id: [
            image for image in candidate_sets[m.device_id]
            if not avoid_duplicates or _path_key(image.path) not in used_images
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
    if avoid_duplicates and len(unique_images) < len(targets):
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
    calendar_rules: list[CalendarRule] | None = None,
    calendar_now: datetime | None = None,
    selection_policy: SelectionPolicy | None = None,
    recent_by_monitor: Mapping[str, tuple[str, ...]] | None = None,
):
    """Plan a shuffle while preserving all monitors that are not targeted.

    A fixed monitor is never a shuffle target. Non-target monitors contribute
    their *current* wallpaper tags to theme limits, when metadata is known.

    C4 calendar rules can *filter* an eligible monitor's existing source, but
    never expand its collection subscriptions or move a file. A preferred
    filter falls back if no image matches; a required filter explains why
    the update cannot proceed. A fixed monitor is still never shuffled.

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
    policy = selection_policy if selection_policy is not None else load_selection_policy()

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

    # C5 metadata is authoritative in SQLite, indexed by stable tag and
    # collection IDs. Folder-mode images absent from SQLite have empty
    # metadata; Require filters never silently accept them.
    with WallpaperCatalog() as catalog:
        memberships = _catalog_memberships(catalog)

    if recent_by_monitor is None and policy.recent_window:
        with RecentHistory() as history:
            if policy.recent_scope == "all":
                global_recent = history.recent_global_events(policy.recent_window)
                recent_by_monitor = {m.device_id: global_recent for m in targets}
            else:
                recent_by_monitor = {
                    m.device_id: history.recent(m.device_id, policy.recent_window)
                    for m in targets
                }
    recent_by_monitor = recent_by_monitor or {}

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

    # Current wallpapers on unchanged displays consume C5 capacity too.
    c5_counts: Counter[tuple[str, str]] = Counter()
    group_counts: dict[str, Counter[tuple[str, str]]] = {
        group.id: Counter() for group in policy.groups
    }
    def image_keys(path: Path) -> set[tuple[str, str]]:
        tags, collections = memberships.get(_path_key(path), (set(), set()))
        return {("tag", tid) for tid in tags} | {("collection", cid) for cid in collections}

    for monitor in detected:
        if monitor.device_id in target_ids:
            continue
        current = get_monitor_wallpaper(monitor.device_id)
        if not current:
            continue
        key = _path_key(Path(current))
        if (policy.collection_limits or policy.tag_limits or
                any(g.collection_limits or g.tag_limits for g in policy.groups)) and key not in memberships:
            raise ValueError(
                "Cannot enforce intelligent theme limits: an unchanged display "
                "uses an image missing from the indexed catalogue. Scan its folder first."
            )
        keys = image_keys(Path(current))
        c5_counts.update(keys)
        for group in policy.groups:
            if monitor.device_id in group.monitor_ids:
                group_counts[group.id].update(keys)

    def over_limit(keys: set[tuple[str, str]], monitor_id: str) -> bool:
        for kind, identity in keys:
            limits_for_kind = (policy.tag_limits if kind == "tag" else policy.collection_limits)
            if identity in limits_for_kind and c5_counts[(kind, identity)] >= limits_for_kind[identity]:
                return True
            for group in policy.groups:
                if monitor_id not in group.monitor_ids:
                    continue
                group_caps = group.tag_limits if kind == "tag" else group.collection_limits
                if identity in group_caps and group_counts[group.id][(kind, identity)] >= group_caps[identity]:
                    return True
        return False

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

    # Calendar policies are passive selectors. Every existing trigger (the
    # button, a daily schedule, or the headless interval agent) consults the
    # current policy; no scheduler needs its own image-selection algorithm.
    calendar = load_calendar_rules() if calendar_rules is None else calendar_rules
    moment = calendar_now or datetime.now().astimezone()
    active_policies = {
        m.device_id: rule for m in targets
        if (rule := active_calendar_rule(calendar, moment, m.device_id)) is not None
    }
    if active_policies:
        with WallpaperCatalog() as catalog:
            images_by_path = {}
            tag_ids_by_image = {}
            collection_ids_by_image = {}
            for monitor in targets:
                rule = active_policies.get(monitor.device_id)
                if rule is None:
                    continue
                matching = []
                for wallpaper in candidate_sets[monitor.device_id]:
                    path_key = _path_key(wallpaper.path)
                    hard_rules = [r for r in policy.rules_for(monitor.device_id)
                                  if r.mode == "require"]
                    if not all(_rule_match(r, memberships.get(path_key, (set(), set())))
                               for r in hard_rules):
                        continue
                    if path_key not in images_by_path:
                        images_by_path[path_key] = catalog.find_image_by_path(wallpaper.path)
                    image = images_by_path[path_key]
                    # Non-indexed folder images have no database tags or
                    # collections. A preferred rule will fall back below.
                    if image is None:
                        continue
                    if image.id not in tag_ids_by_image:
                        tag_ids_by_image[image.id] = {
                            tag.id for tag in catalog.list_image_tags(image.id)
                        }
                    tags = tag_ids_by_image[image.id]
                    if rule.include_tag_ids and not tags.intersection(rule.include_tag_ids):
                        continue  # Any listed included tag may match.
                    if rule.exclude_tag_ids and tags.intersection(rule.exclude_tag_ids):
                        continue
                    if rule.collection_ids:
                        if image.id not in collection_ids_by_image:
                            collection_ids_by_image[image.id] = {
                                item.id for item in catalog.list_image_collections(image.id)
                            }
                        if not collection_ids_by_image[image.id].intersection(rule.collection_ids):
                            continue
                    matching.append(wallpaper)
                if matching:
                    source = sources.get(monitor.device_id)
                    candidate_sets[monitor.device_id] = _order_candidates(
                        matching, source, profiles.get(monitor.device_id, {}),
                        monitor.device_id,
                    )
                elif rule.behavior == "require":
                    raise ValueError(
                        f"Seasonal rule '{rule.name}' has no matching "
                        f"{monitor.orientation} wallpapers for this display. "
                        "Add and scan a tagged image in its assigned source, "
                        "change the rule's filters, or set it to Prefer."
                    )

    # C5 rules operate only within the monitor's already-eligible sources.
    # Require rules filter; Prefer rules affect ordering but never invalidate
    # an otherwise possible combination.
    for monitor in targets:
        mid = monitor.device_id
        hard_rules = [rule for rule in policy.rules_for(mid) if rule.mode == "require"]
        preferred = [rule for rule in policy.rules_for(mid) if rule.mode == "prefer"]
        candidates = [wallpaper for wallpaper in candidate_sets[mid]
                      if all(_rule_match(rule, memberships.get(_path_key(wallpaper.path),
                                                         (set(), set()))) for rule in hard_rules)]
        if not candidates:
            raise ValueError(
                f"Intelligent Require filters leave no {monitor.orientation} "
                f"wallpapers for this display. Check its tags and subscribed collections."
            )
        recent = set(recent_by_monitor.get(mid, ())[
            :policy.recent_window if policy.recent_scope == "monitor" else None
        ]) if policy.recent_window else set()
        if policy.recent_mode == "require":
            candidates = [item for item in candidates if _path_key(item.path) not in recent]
            if not candidates:
                raise ValueError(
                    f"No wallpapers remain outside the last {policy.recent_window} "
                    "choices. Reduce the recent-image window or choose Prefer."
                )
        def preference_score(wallpaper):
            membership = memberships.get(_path_key(wallpaper.path), (set(), set()))
            return (
                int(_path_key(wallpaper.path) not in recent),
                sum(_rule_match(rule, membership) for rule in preferred),
            )
        # Stable sorting retains Random / Next / Previous within each tier.
        candidates.sort(key=preference_score, reverse=True)
        candidate_sets[mid] = candidates

    # Check more constrained monitors first, then backtrack if needed.
    targets.sort(key=lambda m: len(candidate_sets[m.device_id]))
    assignments = {}

    # A greedy first-valid combination can waste a fresh image on a monitor
    # that has alternatives, forcing a second monitor into its recent history.
    # Search for the best *joint* preference score, with a bounded improvement
    # pass once a valid plan exists. Never bound the initial feasibility search.
    optimize = any(
        rule.mode == "prefer" for m in targets for rule in policy.rules_for(m.device_id)
    ) or (policy.recent_mode == "prefer" and any(
        recent_by_monitor.get(m.device_id) for m in targets
    ))
    scores = {}
    for m in targets:
        mid = m.device_id
        recent = set(recent_by_monitor.get(mid, ())[
            :policy.recent_window if policy.recent_scope == "monitor" else None
        ]) if policy.recent_window else set()
        preferred = [rule for rule in policy.rules_for(mid) if rule.mode == "prefer"]
        for item in candidate_sets[mid]:
            key = _path_key(item.path)
            meta = memberships.get(key, (set(), set()))
            scores[(mid, key)] = (
                (1000 if key not in recent else 0) if policy.recent_window else 0
            ) + sum(rule.matches(*meta) for rule in preferred)

    best: dict = {}
    best_score = -1
    visited = 0
    budget = 20000

    def search(index: int, score: int = 0) -> bool:
        nonlocal best, best_score, visited
        if best and (not optimize or visited >= budget):
            return True
        if index == len(targets):
            if score > best_score:
                best = dict(assignments)
                best_score = score
            return not optimize
        m = targets[index]
        for w in candidate_sets[m.device_id]:
            visited += 1
            key = _path_key(w.path)
            if policy.avoid_duplicates and key in used_images:
                continue
            if any(counts[theme] >= limits[theme]
                   for theme in w.themes if theme in limits):
                continue
            keys = image_keys(w.path)
            if over_limit(keys, m.device_id):
                continue
            assignments[m.device_id] = w
            newly_used = key not in used_images
            used_images.add(key)
            counts.update(w.themes)
            c5_counts.update(keys)
            member_groups = [g.id for g in policy.groups if m.device_id in g.monitor_ids]
            for group_id in member_groups:
                group_counts[group_id].update(keys)
            done = search(index + 1, score + scores[(m.device_id, key)])
            del assignments[m.device_id]
            if newly_used:
                used_images.remove(key)
            for theme in w.themes:
                counts[theme] -= 1
            for item in keys:
                c5_counts[item] -= 1
                for group_id in member_groups:
                    group_counts[group_id][item] -= 1
            if done:
                return True
        return False

    search(0)
    if not best:
        if policy.collection_limits or policy.tag_limits or any(
            g.collection_limits or g.tag_limits for g in policy.groups
        ):
            raise ValueError(
                "No combination satisfies the configured C5 collection/tag "
                "limits and available wallpapers. Relax a strict limit, add "
                "more images, or update the affected monitors together."
            )
        raise ValueError(
            _selection_failure_message(
                targets, candidate_sets, used_images, limits, counts, theme_owners,
                avoid_duplicates=policy.avoid_duplicates,
            )
        )

    # `apply_plan` accepts any assignment key paired with its device ID.
    return best, {device_id: device_id for device_id in best}


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
    # Keep history after the entire coordinated update succeeded, never after
    # a partial application or attempted rollback. A history I/O failure must
    # not be reported as a wallpaper-application failure.
    try:
        with RecentHistory() as history:
            history.record({device_ids[key]: wallpaper.path
                            for key, wallpaper in assignments.items()})
    except (OSError, ValueError, sqlite3.Error) as error:
        warnings.warn(f"Wallpapers applied, but recent history was not saved: {error}")
