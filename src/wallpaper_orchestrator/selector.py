
"""Wallpaper selection algorithms."""

import random
from collections.abc import Sequence

from wallpaper_orchestrator.models import Monitor, Wallpaper


def choose_wallpaper(
    monitor: Monitor,
    wallpapers: Sequence[Wallpaper],
    *,
    rng: random.Random | None = None,
) -> Wallpaper:
    """Randomly select an orientation-compatible wallpaper."""

    candidates = [
        wallpaper
        for wallpaper in wallpapers
        if wallpaper.orientation == monitor.orientation
    ]

    if not candidates:
        raise ValueError(
            f"No suitable wallpapers for monitor '{monitor.role}'"
        )

    generator = rng if rng is not None else random.Random()

    return generator.choice(candidates)
