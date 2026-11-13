"""C8.2.1 safety: folder exclusions and user wallpaper restore points."""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.indexer import scan_folder
from wallpaper_orchestrator.restore_points import (
    capture_wallpapers, read_restore_point, restore_wallpapers,
    save_restore_point, pause_for_restore,
)
from wallpaper_orchestrator.automation import AutomationConfig, load_automation, save_automation


class ExcludedImagesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.catalog = WallpaperCatalog(self.root / "catalog.sqlite3")
        self.addCleanup(self.catalog.close)
        self.collection = self.catalog.create_collection("Landscape")
        self.other = self.catalog.create_collection("Other")
        self.folder = self.root / "pictures"
        self.folder.mkdir()
        self.link = self.catalog.link_folder(self.collection.id, self.folder)

    def make_image(self, name="one.png"):
        file = self.folder / name
        Image.new("RGB", (100, 60)).save(file)
        return file

    def test_linked_image_excluded_and_restored_across_scans(self):
        file = self.make_image()
        scan_folder(self.catalog, self.link.id)
        image = self.catalog.find_image_by_path(file)
        self.catalog.add_image_to_collection(self.other.id, image.id)
        self.assertTrue(self.catalog.remove_image_from_collection(self.collection.id, image.id))
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [])
        self.assertEqual(self.catalog.list_excluded_images(self.collection.id), [image])
        self.assertEqual(self.catalog.list_image_collections(image.id), [self.other])
        scan_folder(self.catalog, self.link.id)
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [])
        self.assertTrue(file.is_file())
        self.catalog.restore_excluded_image(self.collection.id, image.id)
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [image])

    def test_manual_only_removal_does_not_exclude_or_delete(self):
        file = self.root / "manual.png"
        Image.new("RGB", (70, 50)).save(file)
        stat = file.stat()
        image = self.catalog.upsert_image(file, width=70, height=50,
                                           size_bytes=stat.st_size,
                                           modified_ns=stat.st_mtime_ns)
        self.catalog.add_image_to_collection(self.collection.id, image.id)
        self.assertFalse(self.catalog.remove_image_from_collection(self.collection.id, image.id))
        self.assertEqual(self.catalog.list_excluded_images(self.collection.id), [])
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [])
        self.assertTrue(file.is_file())

    def test_manual_drop_inside_linked_folder_before_scan_is_excluded(self):
        file = self.make_image()
        stat = file.stat()
        image = self.catalog.upsert_image(file, width=100, height=60,
                                          size_bytes=stat.st_size,
                                          modified_ns=stat.st_mtime_ns)
        self.catalog.add_image_to_collection(self.collection.id, image.id)
        self.assertTrue(self.catalog.remove_image_from_collection(self.collection.id, image.id))
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [])
        scan_folder(self.catalog, self.link.id)
        self.assertEqual(len(self.catalog.list_excluded_images(self.collection.id)), 1)
        file.unlink()
        scan_folder(self.catalog, self.link.id)
        self.assertEqual(self.catalog.list_excluded_images(self.collection.id), [])

    def test_explicit_import_restores_exclusion(self):
        file = self.make_image()
        scan_folder(self.catalog, self.link.id)
        image = self.catalog.find_image_by_path(file)
        self.catalog.remove_image_from_collection(self.collection.id, image.id)
        self.catalog.add_image_to_collection(self.collection.id, image.id)
        self.assertFalse(self.catalog.list_excluded_images(self.collection.id))
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [image])

    def test_missing_file_clears_exclusion_on_full_scan(self):
        file = self.make_image()
        scan_folder(self.catalog, self.link.id)
        image = self.catalog.find_image_by_path(file)
        self.catalog.remove_image_from_collection(self.collection.id, image.id)
        file.unlink()
        scan_folder(self.catalog, self.link.id)
        self.assertEqual(self.catalog.list_excluded_images(self.collection.id), [])

    def test_partial_scan_or_disconnected_folder_preserves_exclusion(self):
        file = self.make_image()
        scan_folder(self.catalog, self.link.id)
        image = self.catalog.find_image_by_path(file)
        self.catalog.remove_image_from_collection(self.collection.id, image.id)
        file.unlink()
        self.catalog.apply_folder_scan(self.link.id, [], prune_missing=False)
        self.assertEqual(len(self.catalog.list_excluded_images(self.collection.id)), 1)
        self.folder.rename(self.root / "disconnected")
        scan_folder(self.catalog, self.link.id)
        self.assertEqual(len(self.catalog.list_excluded_images(self.collection.id)), 1)

    def test_v2_schema_migrates_without_losing_collections(self):
        db_path = self.catalog.path
        self.catalog.close()
        with closing(sqlite3.connect(db_path)) as db:
            with db:
                db.execute("DROP TABLE collection_exclusions")
                db.execute("PRAGMA user_version = 2")
        with WallpaperCatalog(db_path) as upgraded:
            self.assertIn(self.collection, upgraded.list_collections())
            self.assertEqual(upgraded._db.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(upgraded.list_excluded_images(self.collection.id), [])


class RestorePointTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.target = self.root / "restore.json"

    def test_baseline_is_write_once_unless_explicitly_replaced(self):
        self.assertTrue(save_restore_point({"left": "first.png"}, path=self.target))
        self.assertFalse(save_restore_point({"left": "second.png"}, path=self.target))
        self.assertEqual(read_restore_point(self.target), {"left": "first.png"})
        self.assertTrue(save_restore_point({"left": "second.png"}, path=self.target,
                                           overwrite=True))
        self.assertEqual(read_restore_point(self.target), {"left": "second.png"})

    def test_first_capture_reads_all_displays_and_never_overwrites(self):
        discover = Mock(return_value=[SimpleNamespace(device_id="left"),
                                      SimpleNamespace(device_id="right")])
        get = Mock(side_effect=lambda mid: "left.png" if mid == "left" else None)
        self.assertTrue(capture_wallpapers(discover, get, path=self.target))
        self.assertEqual(read_restore_point(self.target), {"left": "left.png", "right": None})
        self.assertFalse(capture_wallpapers(discover, get, path=self.target))
        self.assertEqual(get.call_count, 2)

    def test_restore_skips_missing_offline_and_blank_files(self):
        image = self.root / "original.png"
        image.write_bytes(b"original image bytes")
        apply = Mock()
        saved = {"left": str(image), "right": str(self.root / "absent.png"),
                 "offline": str(image), "blank": None}
        restored, skipped = restore_wallpapers(saved, ["left", "right", "blank"], apply)
        self.assertEqual(restored, ["left"])
        self.assertEqual(len(skipped), 3)
        apply.assert_called_once_with("left", image)
        self.assertTrue(image.is_file())

    def test_pause_disables_background_without_deleting_rules(self):
        save_automation(AutomationConfig(background_enabled=True, paused=False))
        with patch("wallpaper_orchestrator.background.set_background_enabled") as disable, \
             patch("wallpaper_orchestrator.background.stop_agent", return_value=True) as stop:
            # Simulate the background helper's settings change in the mocked call.
            disable.side_effect = lambda enabled: save_automation(
                AutomationConfig(background_enabled=enabled, paused=True)
            )
            pause_for_restore()
        self.assertTrue(load_automation().paused)
        self.assertFalse(load_automation().background_enabled)
        disable.assert_called_once_with(False)
        stop.assert_called_once_with()

    def test_failed_agent_stop_prevents_restore(self):
        with patch("wallpaper_orchestrator.background.stop_agent", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "did not stop"):
                pause_for_restore()
        self.assertTrue(load_automation().paused)


if __name__ == "__main__":
    unittest.main()
