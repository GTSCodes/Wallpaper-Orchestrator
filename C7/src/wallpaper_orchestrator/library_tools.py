"""C6 non-Qt Library utilities: safe imports and exact duplicate detection."""

from collections import defaultdict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
from typing import Iterable

from wallpaper_orchestrator.indexer import SUPPORTED_EXTENSIONS


@dataclass(frozen=True)
class ImportReport:
    copied: tuple[Path, ...]
    skipped: tuple[str, ...]


@dataclass(frozen=True)
class DuplicateReport:
    groups: tuple[tuple[Path, ...], ...]
    unreadable: tuple[str, ...]


def copy_images_into_folder(sources: Iterable[Path | str], destination: Path | str) -> ImportReport:
    """Copy explicitly selected files into an existing linked folder.

    Never delete/move the original, follow directory names from a source,
    overwrite existing files, or silently import unsupported extensions.
    """
    folder = Path(destination)
    if not folder.is_dir():
        raise ValueError(f"Import folder does not exist: {folder}")
    copied, skipped = [], []
    for original in sources:
        source = Path(original)
        if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            skipped.append(f"{source.name}: unsupported format")
            continue
        if source.is_symlink() or not source.is_file():
            skipped.append(f"{source.name}: unavailable or symbolic link")
            continue
        if source.parent.resolve() == folder.resolve():
            skipped.append(f"{source.name}: already in destination folder")
            continue
        stem, suffix = source.stem, source.suffix
        for number in range(1, 10001):
            candidate = folder / (source.name if number == 1 else f"{stem} ({number}){suffix}")
            try:
                with source.open("rb") as input_file, candidate.open("xb") as output:
                    try:
                        shutil.copyfileobj(input_file, output, 1024 * 1024)
                    except BaseException:
                        output.close()
                        candidate.unlink(missing_ok=True)
                        raise
                copied.append(candidate)
                break
            except FileExistsError:
                continue
        else:
            skipped.append(f"{source.name}: too many filename collisions")
    return ImportReport(tuple(copied), tuple(skipped))


def find_exact_duplicates(paths: Iterable[Path | str]) -> DuplicateReport:
    """Hash only same-sized files; detect byte-for-byte duplicates, not similarity."""
    sizes = defaultdict(list)
    failures = []
    for path_value in dict.fromkeys(Path(value) for value in paths):
        try:
            if path_value.is_symlink() or not path_value.is_file():
                failures.append(f"{path_value}: missing or symbolic link")
                continue
            sizes[path_value.stat().st_size].append(path_value)
        except OSError as error:
            failures.append(f"{path_value}: {error}")
    duplicates = defaultdict(list)
    for same_size in sizes.values():
        if len(same_size) < 2:
            continue
        for path in same_size:
            try:
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                duplicates[(path.stat().st_size, digest.digest())].append(path)
            except OSError as error:
                failures.append(f"{path}: {error}")
    groups = tuple(
        tuple(sorted(group, key=lambda path: str(path).casefold()))
        for group in duplicates.values() if len(group) > 1
    )
    return DuplicateReport(tuple(sorted(groups, key=lambda item: str(item[0]).casefold())),
                           tuple(failures))
