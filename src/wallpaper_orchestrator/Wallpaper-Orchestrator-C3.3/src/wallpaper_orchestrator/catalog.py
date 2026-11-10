"""SQLite library catalogue for Wallpaper Orchestrator.

This is deliberately independent from the GUI, Windows COM, and the existing
JSON monitor profiles. It stores *references* to user files, never file bytes.
Discovery and filesystem watching will use this API in later milestones.
"""

import os
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from uuid import uuid4

from wallpaper_orchestrator.catalog_models import (
    Collection,
    FolderLink,
    ImageMetadata,
    ImageRecord,
    Tag,
)


SCHEMA_VERSION = 1


def default_catalog_path() -> Path:
    """Keep the database next to the app's existing per-user settings."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "WallpaperOrchestrator" / "catalog.sqlite3"


def _normalised_path(path: str | Path) -> tuple[str, str]:
    """Return a display path and OS-aware lookup key without requiring it to exist.

    In particular, an unavailable external drive may still be linked. File
    identity is based on its normalised *path*, not a content hash.
    """
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise ValueError("Choose a non-empty path.")
    display = os.path.normpath(os.path.abspath(os.path.expanduser(raw)))
    return display, os.path.normcase(display)


def _normalised_name(name: str) -> tuple[str, str]:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Names must not be empty.")
    display = name.strip()
    return display, display.casefold()


def _image_from_row(row: sqlite3.Row) -> ImageRecord:
    return ImageRecord(
        id=row["id"],
        path=Path(row["path"]),
        width=row["width"],
        height=row["height"],
        size_bytes=row["size_bytes"],
        modified_ns=row["modified_ns"],
        available=bool(row["available"]),
    )


class WallpaperCatalog:
    """A local catalogue with stable IDs and many-to-many relationships.

    Instantiate separately in each thread/process. SQLite transactions protect
    multi-statement updates; no shared global connection is retained.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_catalog_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=10)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.execute("PRAGMA journal_mode = WAL")
            self._initialise_schema()
        except BaseException:
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "WallpaperCatalog":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _initialise_schema(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"Catalogue version {version} is newer than supported "
                f"version {SCHEMA_VERSION}; upgrade Wallpaper Orchestrator."
            )
        if version == SCHEMA_VERSION:
            return

        # A fresh database only. Future migrations will branch on user_version.
        self._db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                name_key TEXT NOT NULL UNIQUE
            );
            CREATE TABLE folder_links (
                id TEXT PRIMARY KEY,
                collection_id TEXT NOT NULL REFERENCES collections(id)
                    ON DELETE CASCADE,
                path TEXT NOT NULL,
                path_key TEXT NOT NULL,
                recursive INTEGER NOT NULL CHECK (recursive IN (0, 1)),
                UNIQUE (collection_id, path_key)
            );
            CREATE TABLE images (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                path_key TEXT NOT NULL UNIQUE,
                width INTEGER NOT NULL CHECK (width > 0),
                height INTEGER NOT NULL CHECK (height > 0),
                size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                modified_ns INTEGER NOT NULL CHECK (modified_ns >= 0),
                available INTEGER NOT NULL DEFAULT 1
                    CHECK (available IN (0, 1))
            );
            CREATE TABLE tags (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                name_key TEXT NOT NULL UNIQUE
            );
            CREATE TABLE image_tags (
                image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (image_id, tag_id)
            );
            -- A folder scan establishes membership through its specific link.
            -- Removing that link must not remove an image or its other links.
            CREATE TABLE folder_images (
                folder_link_id TEXT NOT NULL REFERENCES folder_links(id)
                    ON DELETE CASCADE,
                image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                PRIMARY KEY (folder_link_id, image_id)
            );
            CREATE TABLE manual_collection_images (
                collection_id TEXT NOT NULL REFERENCES collections(id)
                    ON DELETE CASCADE,
                image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                PRIMARY KEY (collection_id, image_id)
            );
            -- Separate from legacy profiles.json until source-mode integration.
            CREATE TABLE monitor_collections (
                monitor_id TEXT NOT NULL,
                collection_id TEXT NOT NULL REFERENCES collections(id)
                    ON DELETE CASCADE,
                PRIMARY KEY (monitor_id, collection_id)
            );
            CREATE INDEX idx_folder_links_collection
                ON folder_links(collection_id);
            CREATE INDEX idx_folder_images_image ON folder_images(image_id);
            CREATE INDEX idx_manual_images_image
                ON manual_collection_images(image_id);
            CREATE INDEX idx_monitor_collections_collection
                ON monitor_collections(collection_id);
            PRAGMA user_version = 1;
            COMMIT;
        """)

    def create_collection(self, name: str) -> Collection:
        display, key = _normalised_name(name)
        record = Collection(uuid4().hex, display)
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO collections(id, name, name_key) VALUES (?, ?, ?)",
                    (record.id, display, key),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"A collection named '{display}' already exists.") from exc
        return record

    def rename_collection(self, collection_id: str, name: str) -> None:
        display, key = _normalised_name(name)
        try:
            with self._db:
                cursor = self._db.execute(
                    "UPDATE collections SET name = ?, name_key = ? WHERE id = ?",
                    (display, key, collection_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError(f"Unknown collection: {collection_id}")
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"A collection named '{display}' already exists.") from exc

    def delete_collection(self, collection_id: str) -> None:
        """Remove catalogue relationships, never the referenced image files."""
        with self._db:
            self._db.execute("DELETE FROM collections WHERE id = ?", (collection_id,))

    def list_collections(self) -> list[Collection]:
        rows = self._db.execute(
            "SELECT id, name FROM collections ORDER BY name_key"
        )
        return [Collection(row["id"], row["name"]) for row in rows]

    def link_folder(
        self, collection_id: str, path: str | Path, *, recursive: bool = True
    ) -> FolderLink:
        if type(recursive) is not bool:
            raise TypeError("recursive must be a boolean")
        display, key = _normalised_path(path)
        record = FolderLink(uuid4().hex, collection_id, Path(display), recursive)
        try:
            with self._db:
                self._db.execute(
                    """INSERT INTO folder_links
                       (id, collection_id, path, path_key, recursive)
                       VALUES (?, ?, ?, ?, ?)""",
                    (record.id, collection_id, display, key, int(recursive)),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "Folder link already exists, or the collection does not exist."
            ) from exc
        return record

    def unlink_folder(self, folder_link_id: str) -> None:
        with self._db:
            self._db.execute(
                "DELETE FROM folder_links WHERE id = ?", (folder_link_id,)
            )

    def list_folder_links(self, collection_id: str) -> list[FolderLink]:
        rows = self._db.execute(
            """SELECT id, collection_id, path, recursive FROM folder_links
               WHERE collection_id = ? ORDER BY path_key""",
            (collection_id,),
        )
        return [
            FolderLink(row["id"], row["collection_id"], Path(row["path"]),
                       bool(row["recursive"]))
            for row in rows
        ]

    def get_folder_link(self, folder_link_id: str) -> FolderLink:
        """Resolve a stable folder ID, including unavailable filesystem paths."""
        row = self._db.execute(
            """SELECT id, collection_id, path, recursive FROM folder_links
               WHERE id = ?""", (folder_link_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown folder link: {folder_link_id}")
        return FolderLink(row["id"], row["collection_id"], Path(row["path"]),
                          bool(row["recursive"]))

    def upsert_image(
        self, path: str | Path, *, width: int, height: int,
        size_bytes: int, modified_ns: int
    ) -> ImageRecord:
        """Record metadata supplied by an indexer; do not touch the actual file."""
        for field, value, minimum in (
            ("width", width, 1), ("height", height, 1),
            ("size_bytes", size_bytes, 0), ("modified_ns", modified_ns, 0),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError(f"{field} must be an integer >= {minimum}.")
        display, key = _normalised_path(path)
        with self._db:
            existing = self._db.execute(
                "SELECT id FROM images WHERE path_key = ?", (key,)
            ).fetchone()
            image_id = existing["id"] if existing else uuid4().hex
            self._db.execute(
                """INSERT INTO images
                   (id, path, path_key, width, height, size_bytes, modified_ns)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path_key) DO UPDATE SET
                     path = excluded.path, width = excluded.width,
                     height = excluded.height, size_bytes = excluded.size_bytes,
                     modified_ns = excluded.modified_ns, available = 1""",
                (image_id, display, key, width, height, size_bytes, modified_ns),
            )
        return self.get_image(image_id)

    def get_image(self, image_id: str) -> ImageRecord:
        row = self._db.execute(
            "SELECT * FROM images WHERE id = ?", (image_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown image: {image_id}")
        return _image_from_row(row)

    def find_image_by_path(self, path: str | Path) -> ImageRecord | None:
        """Look up indexed metadata by a normalised path without decoding it."""
        _, key = _normalised_path(path)
        row = self._db.execute(
            "SELECT * FROM images WHERE path_key = ?", (key,)
        ).fetchone()
        return _image_from_row(row) if row is not None else None

    def list_folder_images(self, folder_link_id: str) -> list[ImageRecord]:
        """List images discovered specifically through this linked folder."""
        rows = self._db.execute(
            """SELECT i.* FROM images AS i
               JOIN folder_images AS fi ON fi.image_id = i.id
               WHERE fi.folder_link_id = ? ORDER BY i.path_key""",
            (folder_link_id,),
        )
        return [_image_from_row(row) for row in rows]

    def set_image_availability(self, image_id: str, available: bool) -> None:
        """Keep metadata for temporarily disconnected drives/cloud folders."""
        if type(available) is not bool:
            raise TypeError("available must be a boolean")
        with self._db:
            cursor = self._db.execute(
                "UPDATE images SET available = ? WHERE id = ?",
                (int(available), image_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown image: {image_id}")

    def register_discovered_image(self, folder_link_id: str, image_id: str) -> None:
        """Associate a scanned image with one *specific* linked folder."""
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO folder_images VALUES (?, ?)",
                (folder_link_id, image_id),
            )

    def unregister_discovered_image(self, folder_link_id: str, image_id: str) -> None:
        with self._db:
            self._db.execute(
                "DELETE FROM folder_images WHERE folder_link_id = ? AND image_id = ?",
                (folder_link_id, image_id),
            )

    def apply_folder_scan(
        self,
        folder_link_id: str,
        inspected: Sequence[ImageMetadata],
        *,
        cached_ids: Iterable[str] = (),
        invalid_ids: Iterable[str] = (),
        unavailable_ids: Iterable[str] = (),
        prune_missing: bool = True,
    ) -> tuple[int, int, int]:
        """Commit one scan atomically without touching original image files.

        A partial or inaccessible scan preserves existing image membership.
        Entries that are corrupt or missing become unavailable without losing
        their persistent image IDs or tags.
        """
        if type(prune_missing) is not bool:
            raise TypeError("prune_missing must be a boolean")
        cached = set(cached_ids)
        invalid = set(invalid_ids)
        unavailable = set(unavailable_ids)
        added = updated = 0
        with self._db:
            if not self._db.execute(
                "SELECT 1 FROM folder_links WHERE id = ?", (folder_link_id,)
            ).fetchone():
                raise KeyError(f"Unknown folder link: {folder_link_id}")

            previous = {
                row["image_id"] for row in self._db.execute(
                    "SELECT image_id FROM folder_images WHERE folder_link_id = ?",
                    (folder_link_id,),
                )
            }
            seen = cached | invalid
            for image in inspected:
                for field, value, minimum in (
                    ("width", image.width, 1), ("height", image.height, 1),
                    ("size_bytes", image.size_bytes, 0),
                    ("modified_ns", image.modified_ns, 0),
                ):
                    if type(value) is not int or value < minimum:
                        raise ValueError(f"{field} must be an integer >= {minimum}.")
                display, key = _normalised_path(image.path)
                row = self._db.execute(
                    "SELECT id FROM images WHERE path_key = ?", (key,)
                ).fetchone()
                image_id = row["id"] if row else uuid4().hex
                if row:
                    updated += 1
                else:
                    added += 1
                self._db.execute(
                    """INSERT INTO images
                       (id, path, path_key, width, height, size_bytes, modified_ns)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(path_key) DO UPDATE SET
                         path = excluded.path, width = excluded.width,
                         height = excluded.height,
                         size_bytes = excluded.size_bytes,
                         modified_ns = excluded.modified_ns, available = 1""",
                    (image_id, display, key, image.width, image.height,
                     image.size_bytes, image.modified_ns),
                )
                seen.add(image_id)

            if not prune_missing:
                seen.update(previous)
            removed = previous - seen
            self._db.executemany(
                "DELETE FROM folder_images WHERE folder_link_id = ? AND image_id = ?",
                [(folder_link_id, image_id) for image_id in removed],
            )
            self._db.executemany(
                "INSERT OR IGNORE INTO folder_images VALUES (?, ?)",
                [(folder_link_id, image_id) for image_id in seen],
            )
            self._db.executemany(
                "UPDATE images SET available = 0 WHERE id = ?",
                [(image_id,) for image_id in invalid | unavailable],
            )
        return added, updated, len(removed)

    def add_image_to_collection(self, collection_id: str, image_id: str) -> None:
        """Manual membership survives unlinking a folder."""
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO manual_collection_images VALUES (?, ?)",
                (collection_id, image_id),
            )

    def remove_image_from_collection(self, collection_id: str, image_id: str) -> None:
        """Remove only manual membership; linked folders still supply images."""
        with self._db:
            self._db.execute(
                """DELETE FROM manual_collection_images
                   WHERE collection_id = ? AND image_id = ?""",
                (collection_id, image_id),
            )

    def list_collection_images(self, collection_id: str) -> list[ImageRecord]:
        """Return each image once, even with overlapping folders and manual links."""
        rows = self._db.execute(
            """SELECT * FROM images WHERE id IN (
                   SELECT fi.image_id FROM folder_images AS fi
                   JOIN folder_links AS fl ON fl.id = fi.folder_link_id
                   WHERE fl.collection_id = ?
                   UNION
                   SELECT image_id FROM manual_collection_images
                   WHERE collection_id = ?
               ) ORDER BY path_key""",
            (collection_id, collection_id),
        )
        return [_image_from_row(row) for row in rows]

    def list_image_collections(self, image_id: str) -> list[Collection]:
        rows = self._db.execute(
            """SELECT id, name FROM collections WHERE id IN (
                   SELECT fl.collection_id FROM folder_images AS fi
                   JOIN folder_links AS fl ON fl.id = fi.folder_link_id
                   WHERE fi.image_id = ?
                   UNION
                   SELECT collection_id FROM manual_collection_images
                   WHERE image_id = ?
               ) ORDER BY name_key""",
            (image_id, image_id),
        )
        return [Collection(row["id"], row["name"]) for row in rows]

    def create_tag(self, name: str) -> Tag:
        display, key = _normalised_name(name)
        tag = Tag(uuid4().hex, display)
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO tags(id, name, name_key) VALUES (?, ?, ?)",
                    (tag.id, display, key),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"A tag named '{display}' already exists.") from exc
        return tag

    def rename_tag(self, tag_id: str, name: str) -> None:
        """Rename a tag in place; associations use its ID and stay intact."""
        display, key = _normalised_name(name)
        try:
            with self._db:
                cursor = self._db.execute(
                    "UPDATE tags SET name = ?, name_key = ? WHERE id = ?",
                    (display, key, tag_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError(f"Unknown tag: {tag_id}")
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"A tag named '{display}' already exists.") from exc

    def delete_tag(self, tag_id: str) -> None:
        """Delete only the label and tag links; never delete an image."""
        with self._db:
            self._db.execute("DELETE FROM tags WHERE id = ?", (tag_id,))

    def list_tags(self) -> list[Tag]:
        rows = self._db.execute("SELECT id, name FROM tags ORDER BY name_key")
        return [Tag(row["id"], row["name"]) for row in rows]

    def tag_image(self, image_id: str, tag_id: str) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO image_tags VALUES (?, ?)",
                (image_id, tag_id),
            )

    def untag_image(self, image_id: str, tag_id: str) -> None:
        with self._db:
            self._db.execute(
                "DELETE FROM image_tags WHERE image_id = ? AND tag_id = ?",
                (image_id, tag_id),
            )

    def list_image_tags(self, image_id: str) -> list[Tag]:
        rows = self._db.execute(
            """SELECT t.id, t.name FROM tags AS t
               JOIN image_tags AS it ON it.tag_id = t.id
               WHERE it.image_id = ? ORDER BY t.name_key""",
            (image_id,),
        )
        return [Tag(row["id"], row["name"]) for row in rows]

    def set_monitor_collections(
        self, monitor_id: str, collection_ids: Iterable[str]
    ) -> None:
        """Atomically replace subscriptions; reject nonexistent collections."""
        if not isinstance(monitor_id, str) or not monitor_id.strip():
            raise ValueError("A monitor ID is required.")
        unique_ids = list(dict.fromkeys(collection_ids))
        with self._db:
            for collection_id in unique_ids:
                found = self._db.execute(
                    "SELECT 1 FROM collections WHERE id = ?", (collection_id,)
                ).fetchone()
                if not found:
                    raise KeyError(f"Unknown collection: {collection_id}")
            self._db.execute(
                "DELETE FROM monitor_collections WHERE monitor_id = ?",
                (monitor_id,),
            )
            self._db.executemany(
                "INSERT INTO monitor_collections VALUES (?, ?)",
                [(monitor_id, collection_id) for collection_id in unique_ids],
            )

    def list_monitor_collections(self, monitor_id: str) -> list[Collection]:
        rows = self._db.execute(
            """SELECT c.id, c.name FROM collections AS c
               JOIN monitor_collections AS mc ON mc.collection_id = c.id
               WHERE mc.monitor_id = ? ORDER BY c.name_key""",
            (monitor_id,),
        )
        return [Collection(row["id"], row["name"]) for row in rows]
