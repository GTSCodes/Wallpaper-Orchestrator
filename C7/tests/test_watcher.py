"""B4 change detection without Qt, COM, or external watchdog dependencies."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.catalog_models import FolderLink
from wallpaper_orchestrator.indexer import scan_folder
from wallpaper_orchestrator.watcher import FolderChangeDetector, snapshot_folder


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.collection_folder = self.root / "Catholic Art"
        self.collection_folder.mkdir()
        self.link = FolderLink("catholic-link", "c", self.collection_folder, True)
        self.detector = FolderChangeDetector()

    def create_image(self, path: Path, size=(1200, 800)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with Image.new("RGB", size, "blue") as image:
            image.save(path)
        return path

    def test_initial_and_unchanged_folder_snapshots(self):
        self.assertEqual(self.detector.poll([self.link]), {self.link.id})
        self.assertEqual(self.detector.poll([self.link]), set())

    def test_new_edit_and_removed_image_trigger_changes(self):
        self.detector.poll([self.link])
        path = self.create_image(self.collection_folder / "disputa.jpg")
        self.assertEqual(self.detector.poll([self.link]), {self.link.id})
        self.assertEqual(self.detector.poll([self.link]), set())
        self.create_image(path, size=(1600, 1000))
        self.assertEqual(self.detector.poll([self.link]), {self.link.id})
        path.unlink()
        self.assertEqual(self.detector.poll([self.link]), {self.link.id})

    def test_move_between_collections_marks_both_folder_links(self):
        other = self.root / "Miles Morales"
        other.mkdir()
        miles = FolderLink("miles-link", "m", other, True)
        original = self.create_image(other / "disputa.jpg")
        self.detector.poll([self.link, miles])
        original.rename(self.collection_folder / original.name)
        self.assertEqual(self.detector.poll([self.link, miles]), {
            self.link.id, miles.id,
        })

    def test_recursive_and_nonrecursive_respect_subdirectories(self):
        direct = FolderLink("direct", "c", self.collection_folder, False)
        recursive = self.link
        detector = FolderChangeDetector()
        detector.poll([direct, recursive])
        self.create_image(self.collection_folder / "nested" / "art.jpg")
        self.assertEqual(detector.poll([direct, recursive]), {recursive.id})

    def test_unrelated_files_are_ignored(self):
        self.detector.poll([self.link])
        (self.collection_folder / "notes.txt").write_text("hello")
        self.assertEqual(self.detector.poll([self.link]), set())

    def test_missing_folder_retains_state_until_reappears(self):
        missing = FolderLink("missing", "c", self.root / "offline", True)
        detector = FolderChangeDetector()
        self.assertEqual(detector.poll([missing]), {"missing"})
        self.assertEqual(snapshot_folder(missing).status, "unavailable")
        self.assertEqual(detector.poll([missing]), set())
        missing.path.mkdir()
        self.create_image(missing.path / "restored.jpg")
        self.assertEqual(detector.poll([missing]), {"missing"})
        self.assertEqual(detector.poll([missing]), set())

    def test_removed_folder_link_is_forgotten_without_scan(self):
        self.detector.poll([self.link])
        self.assertEqual(self.detector.poll([]), set())
        self.assertEqual(self.detector.poll([self.link]), {self.link.id})

    def test_overlapping_identical_links_share_one_walk(self):
        clone = FolderLink("second-link", "other", self.collection_folder, True)
        with patch("wallpaper_orchestrator.watcher.snapshot_folder",
                   wraps=snapshot_folder) as wrapped:
            self.assertEqual(self.detector.poll([self.link, clone]), {
                self.link.id, clone.id,
            })
        self.assertEqual(wrapped.call_count, 1)

    def test_does_not_follow_symlink_images(self):
        target = self.create_image(self.root / "outside.jpg")
        link = self.collection_folder / "shortcut.jpg"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        self.assertEqual(snapshot_folder(self.link).files, ())

    def test_detector_to_indexer_roundtrip_adds_and_unlinks_without_deletion(self):
        database = self.root / "catalog.sqlite3"
        with WallpaperCatalog(database) as catalog:
            collection = catalog.create_collection("Catholic Art")
            link = catalog.link_folder(collection.id, self.collection_folder)
            detector = FolderChangeDetector()
            self.assertEqual(detector.poll([link]), {link.id})
            scan_folder(catalog, link.id)

            image_path = self.create_image(self.collection_folder / "disputa.jpg")
            self.assertEqual(detector.poll([link]), {link.id})
            report = scan_folder(catalog, link.id)
            self.assertEqual(report.added, 1)
            self.assertEqual(len(catalog.list_collection_images(collection.id)), 1)

            other = self.root / "elsewhere"
            other.mkdir()
            moved = other / image_path.name
            image_path.rename(moved)
            self.assertEqual(detector.poll([link]), {link.id})
            report = scan_folder(catalog, link.id)
            self.assertEqual(report.removed, 1)
            self.assertEqual(catalog.list_collection_images(collection.id), [])
            self.assertTrue(moved.is_file(), "The scanner must never delete files")


if __name__ == "__main__":
    unittest.main()
