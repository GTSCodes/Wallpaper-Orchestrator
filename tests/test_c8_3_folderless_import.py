"""C8.3.0 regressions: folderless manual imports and C8.2.1 exclusions."""

import ast
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from wallpaper_orchestrator.catalog import SCHEMA_VERSION, WallpaperCatalog
from wallpaper_orchestrator.indexer import scan_folder
from wallpaper_orchestrator.library_tools import import_images_to_collection


class FolderlessImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.catalog = WallpaperCatalog()
        self.addCleanup(self.catalog.close)
        self.collection = self.catalog.create_collection("Favourites")

    def make_image(self, name="art.png", size=(1200, 800), directory=None):
        path = (directory or self.root) / name
        Image.new("RGB", size, color="blue").save(path)
        return path

    def import_one(self, path, collection_id=None):
        report = import_images_to_collection(
            self.catalog, collection_id or self.collection.id, [path]
        )
        self.assertEqual(report.skipped, ())
        self.assertEqual(report.imported, (path,))
        return self.catalog.find_image_by_path(path)

    def test_folderless_import_records_original_path_and_membership(self):
        path = self.make_image()
        original = path.read_bytes()
        record = self.import_one(path)
        self.assertEqual(self.catalog.list_folder_links(self.collection.id), [])
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [record])
        self.assertEqual(self.catalog.list_image_collections(record.id), [self.collection])
        self.assertEqual((record.width, record.height), (1200, 800))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.root.rglob('art*.png')), [path])

    def test_manual_import_is_idempotent_and_reuses_image_id(self):
        path = self.make_image()
        first = self.import_one(path)
        report = import_images_to_collection(self.catalog, self.collection.id, [path, path])
        self.assertEqual(report.imported, (path,))
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [first])
        self.assertEqual(self.catalog.find_image_by_path(path).id, first.id)
        membership_count = self.catalog._db.execute(
            "SELECT COUNT(*) FROM manual_collection_images WHERE collection_id = ?",
            (self.collection.id,),
        ).fetchone()[0]
        self.assertEqual(membership_count, 1)

    def test_manual_remove_preserves_file_without_exclusion(self):
        path = self.make_image()
        record = self.import_one(path)
        self.assertFalse(self.catalog.remove_image_from_collection(self.collection.id, record.id))
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [])
        self.assertEqual(self.catalog.list_excluded_images(self.collection.id), [])
        self.assertTrue(path.is_file())
        self.assertEqual(self.catalog.get_image(record.id), record)

    def test_membership_survives_database_reopen_and_existing_schema(self):
        path = self.make_image()
        record = self.import_one(path)
        db_path = self.catalog.path
        with WallpaperCatalog(db_path) as reopened:
            self.assertEqual(reopened.list_collection_images(self.collection.id), [record])
            self.assertEqual(reopened._db.execute("PRAGMA user_version").fetchone()[0],
                             SCHEMA_VERSION)
        self.assertEqual(SCHEMA_VERSION, 3)

    def test_invalid_images_and_symlinks_are_skipped_without_membership(self):
        valid = self.make_image()
        bad = self.root / "bad.png"
        bad.write_text("not an image", encoding="utf-8")
        unsupported = self.root / "bad.txt"
        unsupported.write_text("not an image", encoding="utf-8")
        missing = self.root / "missing.png"
        paths = [valid, bad, unsupported, missing]
        symlink = self.root / "shortcut.png"
        try:
            symlink.symlink_to(valid)
        except (OSError, NotImplementedError):
            pass  # Creating symlinks can require elevated privileges on Windows.
        else:
            paths.append(symlink)
        report = import_images_to_collection(self.catalog, self.collection.id, paths)
        self.assertEqual(report.imported, (valid,))
        self.assertEqual(len(report.skipped), len(paths) - 1)
        self.assertEqual(len(self.catalog.list_collection_images(self.collection.id)), 1)

    def test_missing_collection_is_rejected_before_creating_images(self):
        path = self.make_image()
        with self.assertRaises(KeyError):
            import_images_to_collection(self.catalog, "unknown-id", [path])
        self.assertIsNone(self.catalog.find_image_by_path(path))

    def test_linked_folder_exclusion_still_survives_scan(self):
        folder = self.root / "linked"
        folder.mkdir()
        link = self.catalog.link_folder(self.collection.id, folder)
        path = self.make_image(directory=folder)
        scan_folder(self.catalog, link.id)
        record = self.catalog.find_image_by_path(path)
        self.assertTrue(self.catalog.remove_image_from_collection(self.collection.id, record.id))
        scan_folder(self.catalog, link.id)
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [])
        self.assertEqual(self.catalog.list_excluded_images(self.collection.id), [record])
        self.import_one(path)  # Explicit import restores the same exclusion.
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [record])
        self.assertEqual(self.catalog.list_excluded_images(self.collection.id), [])

    def test_mixed_manual_and_linked_images_remain_independent(self):
        folder = self.root / "linked"
        folder.mkdir()
        link = self.catalog.link_folder(self.collection.id, folder)
        folder_path = self.make_image(name="linked.png", directory=folder)
        manual_path = self.make_image(name="manual.png")
        scan_folder(self.catalog, link.id)
        linked = self.catalog.find_image_by_path(folder_path)
        manual = self.import_one(manual_path)
        self.assertEqual(set(self.catalog.list_collection_images(self.collection.id)),
                         {linked, manual})
        self.assertFalse(self.catalog.remove_image_from_collection(self.collection.id,
                                                                   manual.id))
        self.assertTrue(self.catalog.remove_image_from_collection(self.collection.id,
                                                                  linked.id))
        scan_folder(self.catalog, link.id)
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [])
        self.assertEqual(self.catalog.list_excluded_images(self.collection.id), [linked])
        self.assertTrue(folder_path.exists() and manual_path.exists())

    def test_one_image_can_be_imported_into_two_folderless_collections(self):
        other = self.catalog.create_collection("Others")
        path = self.make_image()
        record = self.import_one(path)
        self.import_one(path, other.id)
        self.assertEqual(self.catalog.list_image_collections(record.id),
                         [self.collection, other])
        self.catalog.remove_image_from_collection(self.collection.id, record.id)
        self.assertEqual(self.catalog.list_collection_images(other.id), [record])

    def test_manual_import_before_folder_scan_preserves_link_exclusion_rules(self):
        folder = self.root / "linked"
        folder.mkdir()
        link = self.catalog.link_folder(self.collection.id, folder)
        path = self.make_image(directory=folder)
        record = self.import_one(path)
        self.assertTrue(self.catalog.remove_image_from_collection(self.collection.id,
                                                                  record.id))
        scan_folder(self.catalog, link.id)
        self.assertEqual(self.catalog.list_collection_images(self.collection.id), [])
        self.assertEqual(self.catalog.list_excluded_images(self.collection.id), [record])

    def test_gui_offers_manual_import_instead_of_requiring_linked_folder(self):
        """Source wiring regression independent of Qt availability on CI hosts."""
        source = (Path(__file__).resolve().parents[1] / "src" /
                  "wallpaper_orchestrator" / "library_view.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        panel = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                     and n.name == "LibraryPanel")
        method = next(n for n in panel.body if isinstance(n, ast.FunctionDef)
                      and n.name == "_import_dropped_images")
        calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
        self.assertTrue(any(isinstance(call.func, ast.Name)
                            and call.func.id == "ManualImportWorker" for call in calls))
        self.assertTrue(any(isinstance(call.func, ast.Name)
                            and call.func.id == "ImportWorker" for call in calls))
        self.assertNotIn("Use + Folder to link an existing destination first", source)


if __name__ == "__main__":
    unittest.main()
