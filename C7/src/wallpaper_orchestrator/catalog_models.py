"""Library records, independent of wallpaper selection and platform APIs.

These models describe the *catalogue*, not physical file ownership. Removing a
record or relationship must never delete the user's original image files.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


Orientation = Literal["landscape", "portrait", "square"]


@dataclass(frozen=True)
class Collection:
    id: str
    name: str


@dataclass(frozen=True)
class FolderLink:
    id: str
    collection_id: str
    path: Path
    recursive: bool


@dataclass(frozen=True)
class Tag:
    id: str
    name: str


@dataclass(frozen=True)
class ImageRecord:
    id: str
    path: Path
    width: int
    height: int
    size_bytes: int
    modified_ns: int
    available: bool

    @property
    def orientation(self) -> Orientation:
        if self.width > self.height:
            return "landscape"
        if self.height > self.width:
            return "portrait"
        return "square"


@dataclass(frozen=True)
class ImageMetadata:
    """Validated dimensions and filesystem metadata obtained by the indexer."""

    path: Path
    width: int
    height: int
    size_bytes: int
    modified_ns: int
