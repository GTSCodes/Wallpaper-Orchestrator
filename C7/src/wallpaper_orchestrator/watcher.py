"""B4: lightweight, dependency-free change detection for linked folders.

This module only reads directory entries and file statistics. It never opens
image content, writes to the catalogue, or modifies the user's files.
The Library UI runs it in a daemon worker and hands changed link IDs to the
existing transactional B2 scanner.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from wallpaper_orchestrator.catalog_models import FolderLink
from wallpaper_orchestrator.indexer import SUPPORTED_EXTENSIONS


@dataclass(frozen=True)
class FolderSnapshot:
    """An inexpensive picture of the files relevant to the image indexer."""

    path: str
    recursive: bool
    status: str  # "ok", "partial", "unavailable"
    files: tuple[tuple[str, int, int], ...]


def snapshot_folder(link: FolderLink) -> FolderSnapshot:
    """Notice additions, moves, removals and edits, including nested folders.

    The fingerprint uses size and nanosecond modification time. Folder walks
    do not follow directory symlinks and image symlinks are ignored, matching
    B2 indexing. A transient read error is represented as a partial snapshot.
    """
    root = link.path
    identity = str(root.absolute()).casefold()
    try:
        if not root.is_dir():
            return FolderSnapshot(identity, link.recursive, "unavailable", ())
    except OSError:
        return FolderSnapshot(identity, link.recursive, "unavailable", ())

    files: list[tuple[str, int, int]] = []
    incomplete = False

    def failed(_error: OSError) -> None:
        nonlocal incomplete
        incomplete = True

    for directory, directories, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=failed
    ):
        directories[:] = [
            name for name in directories
            if not (Path(directory) / name).is_symlink()
        ]
        for filename in filenames:
            path = Path(directory) / filename
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS or path.is_symlink():
                continue
            try:
                stat = path.stat()
            except OSError:
                incomplete = True
                continue
            files.append((str(path.absolute()).casefold(), stat.st_size, stat.st_mtime_ns))
        if not link.recursive:
            break

    return FolderSnapshot(
        identity, link.recursive, "partial" if incomplete else "ok",
        tuple(sorted(files)),
    )


class FolderChangeDetector:
    """Track each folder link independently, including changed subscriptions."""

    def __init__(self):
        self._previous: dict[str, FolderSnapshot] = {}

    def poll(self, links: Iterable[FolderLink]) -> set[str]:
        """Return IDs needing a scan; a newly linked folder scans on first poll.

        Removed links are forgotten without requesting scans. Overlapping
        folder links share one filesystem walk per (path, recursive) pair.
        """
        current: dict[str, FolderSnapshot] = {}
        cache: dict[tuple[str, bool], FolderSnapshot] = {}
        changed: set[str] = set()
        for link in links:
            key = (str(link.path.absolute()).casefold(), link.recursive)
            if key not in cache:
                cache[key] = snapshot_folder(link)
            snapshot = cache[key]
            current[link.id] = snapshot
            if self._previous.get(link.id) != snapshot:
                changed.add(link.id)
        self._previous = current
        return changed
