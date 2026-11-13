"""C6 data safety: schema migration, bulk tags and folder defaults."""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.indexer import scan_folder
from wallpaper_orchestrator.library_tools import copy_images_into_folder, find_exact_duplicates


class LibraryC6Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.catalog = WallpaperCatalog()
        self.addCleanup(self.catalog.close)

    def image(self, name):
        path = self.root / name
        Image.new("RGB", (1200, 900), color="blue").save(path)
        return path

    def test_v1_database_upgrades_without_losing_collections(self):
        collection = self.catalog.create_collection("Keep me")
        path = self.catalog.path
        self.catalog.close()
        # Emulate the exact prior-version schema in an isolated copy.
        # Connection.__exit__ commits or rolls back, but does not close the
        # SQLite handle. Windows cannot remove the temp database until closed.
        with closing(sqlite3.connect(path)) as db:
            with db:
                db.execute("DROP TABLE folder_tag_defaults")
                db.execute("DROP TABLE collection_exclusions")
                db.execute("PRAGMA user_version = 1")
        with WallpaperCatalog(path) as upgraded:
            self.assertIn(collection, upgraded.list_collections())
            self.assertEqual(upgraded._db.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_default_tags_apply_to_new_and_existing_folder_images(self):
        collection = self.catalog.create_collection("Art")
        winter = self.catalog.create_tag("Winter")
        season = self.catalog.create_tag("Christmas")
        link = self.catalog.link_folder(collection.id, self.root)
        first = self.image("first.png")
        scan_folder(self.catalog, link.id)
        record = self.catalog.find_image_by_path(first)
        self.catalog.set_folder_default_tags(link.id, [winter.id])
        self.assertIn(winter.id, self.catalog.list_image_tag_ids([record.id])[record.id])
        second = self.image("second.png")
        scan_folder(self.catalog, link.id)
        record2 = self.catalog.find_image_by_path(second)
        self.assertIn(winter.id, self.catalog.list_image_tag_ids([record2.id])[record2.id])
        self.catalog.set_folder_default_tags(link.id, [season.id])
        tags = self.catalog.list_image_tag_ids([record.id, record2.id])
        self.assertTrue(all(winter.id in ids and season.id in ids for ids in tags.values()))
        self.assertEqual(self.catalog.list_folder_default_tags(link.id), [season])
        with self.assertRaises(ValueError):
            self.catalog.set_folder_default_tags(link.id, ["invalid-tag-id"])
        self.assertEqual(self.catalog.list_folder_default_tags(link.id), [season])
        self.assertTrue(first.exists() and second.exists())

    def test_bulk_tags_transactional_and_repeat_safe(self):
        collection = self.catalog.create_collection("Art")
        image1 = self.image("one.png")
        image2 = self.image("two.png")
        records = [self.catalog.upsert_image(path, width=1200, height=900,
                    size_bytes=path.stat().st_size, modified_ns=path.stat().st_mtime_ns)
                   for path in (image1, image2)]
        tag = self.catalog.create_tag("Gold")
        ids = [record.id for record in records]
        self.catalog.tag_images(iter(ids), iter([tag.id]))
        self.catalog.tag_images(ids, [tag.id])
        self.assertTrue(all(self.catalog.list_image_tag_ids(ids)[i] == {tag.id} for i in ids))
        with self.assertRaises(ValueError):
            self.catalog.tag_images(ids, [tag.id, "missing-tag"])
        self.assertTrue(all(self.catalog.list_image_tag_ids(ids)[i] == {tag.id} for i in ids))
        self.catalog.untag_images(ids, [tag.id])
        self.assertTrue(all(not v for v in self.catalog.list_image_tag_ids(ids).values()))

    def test_import_preserves_original_and_does_not_overwrite(self):
        destination = self.root / "linked"
        destination.mkdir()
        source = self.image("family.png")
        first = copy_images_into_folder([source], destination)
        second = copy_images_into_folder([source], destination)
        self.assertEqual(first.copied[0].name, "family.png")
        self.assertEqual(second.copied[0].name, "family (2).png")
        self.assertEqual(source.read_bytes(), first.copied[0].read_bytes())
        self.assertTrue(source.exists())
        self.assertEqual(len(copy_images_into_folder(["not-an-image.exe"], destination).skipped), 1)

    def test_hash_based_duplicates_ignore_same_size_different_content(self):
        original = self.image("original.png")
        copy = self.root / "copy.png"
        copy.write_bytes(original.read_bytes())
        different = self.root / "other.png"
        different.write_bytes(b"0" * original.stat().st_size)
        groups = find_exact_duplicates([original, copy, different])
        self.assertEqual(len(groups.groups), 1)
        self.assertEqual(set(groups.groups[0]), {original, copy})


if __name__ == "__main__":
    unittest.main()
