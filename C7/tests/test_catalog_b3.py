"""B3 catalogue changes used by the new Library interface."""

import tempfile
import unittest
from pathlib import Path

from wallpaper_orchestrator.catalog import WallpaperCatalog


class TagManagementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.catalog = WallpaperCatalog(
            Path(self.temporary.name) / "catalog.sqlite3"
        )
        self.addCleanup(self.catalog.close)

    def test_rename_tag_preserves_image_membership(self):
        tag = self.catalog.create_tag("Dark")
        image = self.catalog.upsert_image(
            Path(self.temporary.name) / "image.jpg", width=1080, height=1920,
            size_bytes=2048, modified_ns=1,
        )
        self.catalog.tag_image(image.id, tag.id)
        self.catalog.rename_tag(tag.id, "Night")
        self.assertEqual(self.catalog.list_image_tags(image.id)[0].id, tag.id)
        self.assertEqual(self.catalog.list_image_tags(image.id)[0].name, "Night")

    def test_duplicate_rename_rolls_back(self):
        first = self.catalog.create_tag("Religious")
        second = self.catalog.create_tag("Dark")
        with self.assertRaises(ValueError):
            self.catalog.rename_tag(first.id, "dark")
        self.assertEqual(
            {t.name for t in self.catalog.list_tags()},
            {"Religious", "Dark"},
        )
        self.assertEqual(len(self.catalog.list_tags()), 2)

    def test_delete_tag_removes_associations_but_retains_image(self):
        tag = self.catalog.create_tag("Minimalist")
        image = self.catalog.upsert_image(
            Path(self.temporary.name) / "image.png", width=100, height=100,
            size_bytes=100, modified_ns=1,
        )
        self.catalog.tag_image(image.id, tag.id)
        self.catalog.delete_tag(tag.id)
        self.assertEqual(self.catalog.list_image_tags(image.id), [])
        self.assertEqual(self.catalog.get_image(image.id), image)


if __name__ == "__main__":
    unittest.main()
