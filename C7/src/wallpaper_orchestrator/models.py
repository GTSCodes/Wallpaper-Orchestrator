
"""Data models used by the wallpaper selection engine."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


Orientation = Literal["landscape", "portrait", "square"]


@dataclass(frozen=True)
class Wallpaper:
    """An image available for wallpaper selection."""

    path: Path
    orientation: Orientation
    themes: frozenset[str]


@dataclass(frozen=True)
class Monitor:
    """A logical monitor participating in wallpaper selection."""

    role: str
    orientation: Orientation
