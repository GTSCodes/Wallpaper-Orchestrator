"""Tests for B2 on-demand indexing; all files are disposable fixtures."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.catalog_models import ImageMetadata
from wallpaper_orchestrator.indexer import (
    main, resolve_folder_link, scan_all, scan_folder,
)


class IndexerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.catalog = WallpaperCatalog(self.root / "catalog.sqlite3")
        self.addCleanup(self.catalog.close)
        self.collection = self.catalog.create_collection("Catholic")
        self.folder = self.root / "Catholic"
        self.folder.mkdir()
        self.link = self.catalog.link_folder(self.collection.id, self.folder)

    def create_image(self, relative: str, size=(800, 1200), format=None) -> Path:
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with Image.new("RGB", size, "navy") as image:
            image.save(path, format=format)
        return path

    def test_initial_scan_reads_metadata_and_associates_images(self):
        self.create_image("portrait.png", (1080, 1920))
        self.create_image("sub/landscape.jpg", (1920, 1080))
        report = scan_folder(self.catalog, self.link.id)
        self.assertEqual((report.status, report.added, report.cached), ("ok", 2, 0))
        records = self.catalog.list_collection_images(self.collection.id)
        self.assertEqual({r.orientation for r in records}, {"portrait", "landscape"})
        self.assertEqual(len(records), 2)

    def test_early_catalog_without_get_folder_link_still_scans(self):
        """A mixed B1/C3 installation must not disable background indexing."""
        class LegacyCatalogue:
            def __init__(self, wrapped):
                self.wrapped = wrapped

            def __getattr__(self, name):
                if name == "get_folder_link":
                    raise AttributeError(name)
                return getattr(self.wrapped, name)

        legacy = LegacyCatalogue(self.catalog)
        self.create_image("legacy.png")
        self.assertEqual(resolve_folder_link(legacy, self.link.id).path, self.folder)
        result = scan_folder(legacy, self.link.id)
        self.assertEqual((result.status, result.added), ("ok", 1))
        with self.assertRaises(KeyError):
            resolve_folder_link(legacy, "missing-id")

    def test_repeat_scan_reuses_cached_dimensions_without_pillow(self):
        self.create_image("same.png")
        scan_folder(self.catalog, self.link.id)
        with patch("wallpaper_orchestrator.indexer._read_metadata") as read:
            result = scan_folder(self.catalog, self.link.id)
            read.assert_not_called()
        self.assertEqual((result.added, result.updated, result.cached), (0, 0, 1))

    def test_changed_image_reindexes_without_losing_id_or_tag(self):
        path = self.create_image("changing.png", (800, 1200))
        scan_folder(self.catalog, self.link.id)
        first = self.catalog.find_image_by_path(path)
        dark = self.catalog.create_tag("Dark")
        self.catalog.tag_image(first.id, dark.id)
        self.create_image("changing.png", (1500, 800))
        # Force a different modified_ns even on low-resolution filesystems.
        os.utime(path, ns=(path.stat().st_atime_ns,
                           path.stat().st_mtime_ns + 2_000_000_000))
        result = scan_folder(self.catalog, self.link.id)
        updated = self.catalog.find_image_by_path(path)
        self.assertEqual((result.added, result.updated), (0, 1))
        self.assertEqual(updated.id, first.id)
        self.assertEqual(updated.orientation, "landscape")
        self.assertEqual(self.catalog.list_image_tags(first.id), [dark])

    def test_new_and_deleted_images_adjust_membership_not_physical_files(self):
        old = self.create_image("old.png")
        scan_folder(self.catalog, self.link.id)
        previous = self.catalog.find_image_by_path(old)
        old.unlink()  # The test simulates user deletion; the indexer never does it.
        new = self.create_image("new.png")
        report = scan_folder(self.catalog, self.link.id)
        self.assertEqual((report.added, report.removed), (1, 1))
        self.assertEqual([image.path for image in
                          self.catalog.list_collection_images(self.collection.id)],
                         [new])
        self.assertFalse(self.catalog.get_image(previous.id).available)
        self.assertTrue(new.is_file())

    def test_multiple_collections_share_one_image_record(self):
        image_path = self.create_image("st-michael.png")
        favourites = self.catalog.create_collection("Favourites")
        second = self.catalog.link_folder(favourites.id, self.folder)
        results = scan_all(self.catalog)
        self.assertEqual(sum(report.added for report in results), 1)
        self.assertEqual(sum(report.cached for report in results), 1)
        first_record = self.catalog.list_collection_images(self.collection.id)[0]
        second_record = self.catalog.list_collection_images(favourites.id)[0]
        self.assertEqual(first_record.id, second_record.id)
        self.assertEqual(first_record.path, image_path)
        self.assertEqual(len(self.catalog.list_folder_images(second.id)), 1)

    def test_non_recursive_folder_ignores_subdirectories(self):
        self.catalog.unlink_folder(self.link.id)
        self.link = self.catalog.link_folder(
            self.collection.id, self.folder, recursive=False
        )
        self.create_image("top.png")
        self.create_image("nested/bottom.png")
        report = scan_folder(self.catalog, self.link.id)
        self.assertEqual(report.added, 1)

    def test_missing_folder_retains_membership_and_recovers(self):
        path = self.create_image("sometimes-offline.png")
        scan_folder(self.catalog, self.link.id)
        record = self.catalog.find_image_by_path(path)
        self.folder.rename(self.root / "TemporarilyDisconnected")
        offline = scan_folder(self.catalog, self.link.id)
        self.assertEqual(offline.status, "unavailable")
        self.assertEqual(len(self.catalog.list_folder_images(self.link.id)), 1)
        self.assertFalse(self.catalog.get_image(record.id).available)
        (self.root / "TemporarilyDisconnected").rename(self.folder)
        recovered = scan_folder(self.catalog, self.link.id)
        self.assertEqual(recovered.updated, 1)
        self.assertTrue(self.catalog.get_image(record.id).available)

    def test_corrupt_image_is_skipped_and_retried(self):
        bad = self.folder / "corrupt.png"
        bad.write_bytes(b"not really a PNG")
        first = scan_folder(self.catalog, self.link.id)
        self.assertEqual((first.skipped, first.added), (1, 0))
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [])
        self.create_image("corrupt.png")
        second = scan_folder(self.catalog, self.link.id)
        self.assertEqual(second.added, 1)
        original = self.catalog.find_image_by_path(bad)
        bad.write_bytes(b"bad replacement")
        third = scan_folder(self.catalog, self.link.id)
        self.assertEqual(third.skipped, 1)
        self.assertFalse(self.catalog.get_image(original.id).available)
        self.assertEqual(len(self.catalog.list_collection_images(self.collection.id)), 1)

    def test_exif_rotation_changes_effective_dimensions(self):
        path = self.folder / "rotated.jpg"
        with Image.new("RGB", (1200, 800)) as image:
            exif = Image.Exif()
            exif[274] = 6  # 90-degree rotation
            image.save(path, exif=exif)
        scan_folder(self.catalog, self.link.id)
        record = self.catalog.find_image_by_path(path)
        self.assertEqual((record.width, record.height), (800, 1200))
        self.assertEqual(record.orientation, "portrait")

    def test_partial_directory_walk_does_not_prune_old_links(self):
        old = self.create_image("old.png")
        scan_folder(self.catalog, self.link.id)

        def incomplete_walk(_link, walk_errors):
            walk_errors.append("Access denied to nested folder")
            return iter(())

        with patch("wallpaper_orchestrator.indexer._image_paths",
                   side_effect=incomplete_walk):
            result = scan_folder(self.catalog, self.link.id)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.removed, 0)
        self.assertEqual(self.catalog.list_folder_images(self.link.id)[0].path, old)

    def test_batch_commit_is_transactional_on_invalid_metadata(self):
        good = ImageMetadata(self.root / "valid.png", 800, 600, 10, 1)
        bad = ImageMetadata(self.root / "invalid.png", 0, 600, 10, 1)
        with self.assertRaises(ValueError):
            self.catalog.apply_folder_scan(self.link.id, [good, bad])
        self.assertIsNone(self.catalog.find_image_by_path(good.path))

    def test_cli_supports_creating_linking_and_scanning(self):
        other_db = self.root / "cli.sqlite3"
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--database", str(other_db),
                                   "create-collection", "Nature"]), 0)
        with WallpaperCatalog(other_db) as another:
            collection_id = another.list_collections()[0].id
        self.create_image("for-cli.png")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--database", str(other_db), "link-folder",
                                   collection_id, str(self.folder)]), 0)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--database", str(other_db), "scan"]), 0)
        with WallpaperCatalog(other_db) as another:
            self.assertEqual(len(another.list_collection_images(collection_id)), 1)


if __name__ == "__main__":
    unittest.main()
