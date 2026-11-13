
"""Wallpaper selection algorithms."""

import random
from collections import Counter
from collections.abc import Sequence, Mapping

from wallpaper_orchestrator.models import Monitor, Wallpaper



def choose_wallpaper(
    monitor: Monitor,
    wallpapers: Sequence[Wallpaper],
    *,
    existing: Sequence[Wallpaper] = (),
    theme_limits: Mapping[str, int] | None = None,
    rng: random.Random | None = None,
) -> Wallpaper:
    """Randomly select an orientation-compatible wallpaper."""

    
    limits = theme_limits if theme_limits is not None else {}

    theme_counts = Counter(
        theme
        for wallpaper in existing
        for theme in wallpaper.themes
    )


    
    candidates = [
        wallpaper
        for wallpaper in wallpapers
        if wallpaper.orientation == monitor.orientation
        and all(
            theme_counts[theme] < limits[theme]
            for theme in wallpaper.themes
            if theme in limits
        )
    ]


    if not candidates:
        raise ValueError(
            f"No suitable wallpapers for monitor '{monitor.role}'"
        )

    generator = rng if rng is not None else random.Random()

    return generator.choice(candidates)


def choose_for_monitors(
    monitors: Sequence[Monitor],
    wallpapers: Sequence[Wallpaper],
    *,
    theme_limits: Mapping[str, int] | None = None,
    rng: random.Random | None = None,
) -> dict[str, Wallpaper]:
    """Find a valid wallpaper combination for multiple monitors."""

    limits = theme_limits if theme_limits is not None else {}
    generator = rng if rng is not None else random.Random()

    assignments: dict[str, Wallpaper] = {}
    theme_counts: Counter[str] = Counter()

    if len({monitor.role for monitor in monitors}) != len(monitors):
        raise ValueError("Monitor roles must be unique.")

    if any(limit < 0 for limit in limits.values()):
        raise ValueError("Theme limits cannot be negative.")

    def search(index: int) -> bool:
        if index == len(monitors):
            return True

        monitor = monitors[index]

        candidates = [
            wallpaper
            for wallpaper in wallpapers
            if wallpaper.orientation == monitor.orientation
        ]

        generator.shuffle(candidates)

        for wallpaper in candidates:
            if any(
                wallpaper.path == used.path
                for used in assignments.values()
            ):
                continue

            if any(
                theme_counts[theme] >= limits[theme]
                for theme in wallpaper.themes
                if theme in limits
            ):
                continue

            assignments[monitor.role] = wallpaper
            theme_counts.update(wallpaper.themes)

            if search(index + 1):
                return True

            for theme in wallpaper.themes:
                theme_counts[theme] -= 1

            del assignments[monitor.role]

        return False

    if not search(0):
        raise ValueError(
            "No valid wallpaper combination satisfies the rules."
        )

    return assignments
