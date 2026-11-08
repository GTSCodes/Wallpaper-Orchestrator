
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
