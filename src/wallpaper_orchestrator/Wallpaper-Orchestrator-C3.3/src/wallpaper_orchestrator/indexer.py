"""On-demand wallpaper indexing for linked folders (Milestone B2).

The indexer never moves, renames, or deletes user images. Only the SQLite
catalogue is updated. Continuous watching belongs to milestone B4.
"""

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterator

from PIL import Image, UnidentifiedImageError

from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.catalog_models import FolderLink, ImageMetadata


SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})


@dataclass(frozen=True)
class IndexReport:
    folder_link_id: str
    folder: Path
    status: str  # "ok", "partial", or "unavailable"
    scanned: int = 0
    added: int = 0
    updated: int = 0
    cached: int = 0
    skipped: int = 0
    removed: int = 0
    errors: tuple[str, ...] = ()


def _image_paths(link: FolderLink, walk_errors: list[str]) -> Iterator[Path]:
    """Walk without following directory or file symlinks outside the root."""
    root = link.path

    def on_error(error: OSError) -> None:
        walk_errors.append(str(error))

    for directory, directories, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=on_error
    ):
        directories[:] = [
            name for name in directories
            if not (Path(directory) / name).is_symlink()
        ]
        for filename in filenames:
            image_path = Path(directory) / filename
            if (image_path.suffix.lower() in SUPPORTED_EXTENSIONS
                    and not image_path.is_symlink()):
                yield image_path
        if not link.recursive:
            break


def _read_metadata(path: Path, before: os.stat_result) -> ImageMetadata:
    """Verify an image and account for EXIF rotations without changing it."""
    with Image.open(path) as image:
        width, height = image.size
        # Pillow requires verify() immediately after opening on some formats.
        image.verify()
    with Image.open(path) as image:
        if image.getexif().get(274) in {5, 6, 7, 8}:
            width, height = height, width

    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError("Image changed while it was being indexed; retry later")
    return ImageMetadata(
        path=path, width=width, height=height,
        size_bytes=after.st_size, modified_ns=after.st_mtime_ns,
    )


def resolve_folder_link(catalog: WallpaperCatalog, folder_link_id: str) -> FolderLink:
    """Find a linked folder, including catalogues from early B1 versions.

    Some installations still have a catalog.py without get_folder_link().
    The listing API predates B2 and is sufficient to resolve the same stable
    folder ID. This compatibility path never modifies the user's database.
    """
    direct = getattr(catalog, "get_folder_link", None)
    if callable(direct):
        return direct(folder_link_id)
    for collection in catalog.list_collections():
        for link in catalog.list_folder_links(collection.id):
            if link.id == folder_link_id:
                return link
    raise KeyError(f"Unknown folder link: {folder_link_id}")


def scan_folder(catalog: WallpaperCatalog, folder_link_id: str) -> IndexReport:
    """Synchronise one linked directory with its cached catalogue records.

    An unavailable root is left untouched. If a walk is incomplete, existing
    associations are preserved rather than mistaken for deleted files.
    """
    link = resolve_folder_link(catalog, folder_link_id)
    if not link.path.is_dir():
        previous = catalog.list_folder_images(folder_link_id)
        catalog.apply_folder_scan(
            folder_link_id, (),
            cached_ids=(image.id for image in previous),
            unavailable_ids=(image.id for image in previous
                             if not image.path.is_file()),
            prune_missing=False,
        )
        return IndexReport(
            folder_link_id, link.path, "unavailable",
            errors=("Folder is missing or inaccessible; retained existing index",),
        )

    previous = catalog.list_folder_images(folder_link_id)
    inspected: list[ImageMetadata] = []
    cached_ids: set[str] = set()
    invalid_ids: set[str] = set()
    errors: list[str] = []
    walk_errors: list[str] = []
    scanned = skipped = 0

    for path in _image_paths(link, walk_errors):
        scanned += 1
        existing = catalog.find_image_by_path(path)
        try:
            stat = path.stat()
            # An unchanged, available image needs no second Pillow decode.
            if (existing is not None and existing.available
                    and existing.size_bytes == stat.st_size
                    and existing.modified_ns == stat.st_mtime_ns):
                cached_ids.add(existing.id)
                continue
            inspected.append(_read_metadata(path, stat))
        except (OSError, ValueError, SyntaxError, UnidentifiedImageError,
                Image.DecompressionBombError) as error:
            skipped += 1
            errors.append(f"{path}: {error}")
            if existing is not None:
                invalid_ids.add(existing.id)

    # Only missing files are globally unavailable. A file can cease belonging
    # to this folder while remaining a valid image elsewhere.
    seen = cached_ids | invalid_ids | {
        cached.id for image in inspected
        if (cached := catalog.find_image_by_path(image.path)) is not None
    }
    unavailable_ids = {
        image.id for image in previous
        if image.id not in seen and not image.path.is_file()
    }
    partial = bool(walk_errors)
    added, updated, removed = catalog.apply_folder_scan(
        folder_link_id, inspected,
        cached_ids=cached_ids, invalid_ids=invalid_ids,
        unavailable_ids=unavailable_ids,
        prune_missing=not partial,
    )
    return IndexReport(
        folder_link_id=folder_link_id,
        folder=link.path,
        status="partial" if partial else "ok",
        scanned=scanned,
        added=added,
        updated=updated,
        cached=len(cached_ids),
        skipped=skipped,
        removed=removed,
        errors=tuple(walk_errors + errors),
    )


def scan_all(
    catalog: WallpaperCatalog, *, collection_id: str | None = None
) -> list[IndexReport]:
    """Scan all linked folders, or only folders for the selected collection."""
    collections = catalog.list_collections()
    if collection_id is not None:
        collections = [c for c in collections if c.id == collection_id]
        if not collections:
            raise KeyError(f"Unknown collection: {collection_id}")
    return [
        scan_folder(catalog, folder.id)
        for collection in collections
        for folder in catalog.list_folder_links(collection.id)
    ]


def main(argv: list[str] | None = None) -> int:
    """A small standalone CLI for exercising B2 before the Library GUI exists."""
    parser = argparse.ArgumentParser(description="Wallpaper library indexer")
    parser.add_argument("--database", type=Path, help="Override catalog.sqlite3 path")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-collection", help="Create a collection")
    create.add_argument("name")
    link = commands.add_parser("link-folder", help="Attach a folder to a collection")
    link.add_argument("collection_id")
    link.add_argument("folder", type=Path)
    link.add_argument("--non-recursive", action="store_true")
    commands.add_parser("list", help="List collections and linked folders")
    scan = commands.add_parser("scan", help="Update the image index")
    scan.add_argument("--collection", help="Scan just one collection ID")
    args = parser.parse_args(argv)

    try:
        with WallpaperCatalog(args.database) as catalog:
            if args.command == "create-collection":
                collection = catalog.create_collection(args.name)
                print(f"Created {collection.name} (ID: {collection.id})")
            elif args.command == "link-folder":
                folder = catalog.link_folder(
                    args.collection_id, args.folder,
                    recursive=not args.non_recursive,
                )
                print(f"Linked {folder.path} (ID: {folder.id})")
            elif args.command == "list":
                for collection in catalog.list_collections():
                    print(f"{collection.name} (ID: {collection.id})")
                    for folder in catalog.list_folder_links(collection.id):
                        print(f"  {folder.path}")
            elif args.command == "scan":
                reports = scan_all(catalog, collection_id=args.collection)
                if not reports:
                    print("No linked folders yet. Create a collection and link a folder.")
                for result in reports:
                    print(
                        f"{result.folder}: {result.status} | "
                        f"{result.added} new, {result.updated} updated, "
                        f"{result.cached} cached, {result.removed} unlinked, "
                        f"{result.skipped} skipped"
                    )
                    for error in result.errors:
                        print(f"  Warning: {error}")
                return 1 if any(r.status != "ok" for r in reports) else 0
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
