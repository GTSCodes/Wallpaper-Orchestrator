"""Run with: PYTHONPATH=src python -m unittest discover -s tests -v"""

import sqlite3
import tempfile
import unittest
import json
from contextlib import closing
from pathlib import Path

from wallpaper_orchestrator.catalog import WallpaperCatalog


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db_path = self.root / "database" / "catalog.sqlite3"
        self.catalog = WallpaperCatalog(self.db_path)
        self.addCleanup(self.catalog.close)

    def add_image(self, name: str = "saint.jpg", *, width: int = 1080,
                  height: int = 1920):
        return self.catalog.upsert_image(
            self.root / name, width=width, height=height,
            size_bytes=1024, modified_ns=42,
        )

    def test_collections_have_stable_ids_and_unique_casefolded_names(self):
        original = self.catalog.create_collection("  Catholic  ")
        with self.assertRaises(ValueError):
            self.catalog.create_collection("cAtHoLiC")
        self.catalog.rename_collection(original.id, "Religious Art")
        self.assertEqual(self.catalog.list_collections()[0].id, original.id)
        self.assertEqual(self.catalog.list_collections()[0].name, "Religious Art")

    def test_one_collection_can_link_several_even_unavailable_folders(self):
        collection = self.catalog.create_collection("Nature")
        a = self.catalog.link_folder(collection.id, self.root / "offline-a")
        b = self.catalog.link_folder(collection.id, self.root / "offline-b",
                                     recursive=False)
        links = self.catalog.list_folder_links(collection.id)
        self.assertEqual({link.id for link in links}, {a.id, b.id})
        self.assertFalse(next(link.recursive for link in links if link.id == b.id))
        with self.assertRaises(ValueError):
            self.catalog.link_folder(collection.id, self.root / "offline-a" / ".")

    def test_same_file_appears_once_per_collection_with_overlap(self):
        catholic = self.catalog.create_collection("Catholic")
        favourites = self.catalog.create_collection("Favourites")
        folder = self.root / "Art"
        a = self.catalog.link_folder(catholic.id, folder)
        b = self.catalog.link_folder(catholic.id, folder / "Subfolder")
        c = self.catalog.link_folder(favourites.id, folder)
        image = self.add_image("Art/Subfolder/saint.jpg")
        self.catalog.register_discovered_image(a.id, image.id)
        self.catalog.register_discovered_image(b.id, image.id)
        self.catalog.register_discovered_image(c.id, image.id)
        self.assertEqual([i.id for i in self.catalog.list_collection_images(catholic.id)],
                         [image.id])
        self.assertEqual([i.id for i in self.catalog.list_collection_images(favourites.id)],
                         [image.id])
        self.assertEqual({c.id for c in self.catalog.list_image_collections(image.id)},
                         {catholic.id, favourites.id})

        # Removing one overlapping link doesn't remove its other memberships.
        self.catalog.unlink_folder(a.id)
        self.assertEqual(len(self.catalog.list_collection_images(catholic.id)), 1)
        self.catalog.unlink_folder(b.id)
        self.assertEqual(self.catalog.list_collection_images(catholic.id), [])
        self.assertEqual(self.catalog.get_image(image.id), image)

    def test_manual_membership_survives_folder_unlink(self):
        collection = self.catalog.create_collection("Portrait")
        linked = self.catalog.link_folder(collection.id, self.root / "Pictures")
        image = self.add_image()
        self.catalog.register_discovered_image(linked.id, image.id)
        self.catalog.add_image_to_collection(collection.id, image.id)
        self.catalog.unlink_folder(linked.id)
        self.assertEqual(self.catalog.list_collection_images(collection.id), [image])
        self.catalog.remove_image_from_collection(collection.id, image.id)
        self.assertEqual(self.catalog.list_collection_images(collection.id), [])

    def test_multiple_tags_remain_independent_of_collections(self):
        image = self.add_image()
        dark = self.catalog.create_tag("Dark")
        baroque = self.catalog.create_tag("Baroque")
        with self.assertRaises(ValueError):
            self.catalog.create_tag("dark")
        self.catalog.tag_image(image.id, dark.id)
        self.catalog.tag_image(image.id, baroque.id)
        self.catalog.tag_image(image.id, dark.id)
        self.assertEqual({tag.id for tag in self.catalog.list_image_tags(image.id)},
                         {dark.id, baroque.id})
        self.catalog.untag_image(image.id, dark.id)
        self.assertEqual(self.catalog.list_image_tags(image.id), [baroque])

    def test_monitor_subscriptions_survive_rename_and_replace_atomically(self):
        one = self.catalog.create_collection("Catholic")
        two = self.catalog.create_collection("Marvel")
        self.catalog.set_monitor_collections("device-id-123", [one.id, two.id, one.id])
        self.catalog.rename_collection(one.id, "Religious")
        self.assertEqual(
            {c.id for c in self.catalog.list_monitor_collections("device-id-123")},
            {one.id, two.id},
        )
        with self.assertRaises(KeyError):
            self.catalog.set_monitor_collections("device-id-123", [two.id, "missing"])
        self.assertEqual(len(self.catalog.list_monitor_collections("device-id-123")), 2)
        self.catalog.set_monitor_collections("device-id-123", [two.id])
        self.assertEqual(self.catalog.list_monitor_collections("device-id-123"), [two])

    def test_image_upsert_preserves_identity_and_updates_orientation(self):
        portrait = self.add_image()
        self.assertEqual(portrait.orientation, "portrait")
        updated = self.catalog.upsert_image(
            portrait.path, width=2560, height=1440, size_bytes=2048,
            modified_ns=84,
        )
        self.assertEqual(portrait.id, updated.id)
        self.assertEqual(updated.orientation, "landscape")
        self.assertEqual(updated.size_bytes, 2048)
        self.catalog.set_image_availability(updated.id, False)
        self.assertFalse(self.catalog.get_image(updated.id).available)
        # Rediscovery makes the image available again.
        self.add_image()
        self.assertTrue(self.catalog.get_image(updated.id).available)

    def test_deleting_collection_never_deletes_user_files_or_image_index(self):
        file = self.root / "actual-image.jpg"
        file.write_bytes(b"example bytes are never interpreted by B1")
        image = self.catalog.upsert_image(
            file, width=800, height=600, size_bytes=file.stat().st_size,
            modified_ns=file.stat().st_mtime_ns,
        )
        collection = self.catalog.create_collection("Temporary")
        self.catalog.add_image_to_collection(collection.id, image.id)
        self.catalog.delete_collection(collection.id)
        self.assertTrue(file.is_file())
        self.assertEqual(self.catalog.get_image(image.id).id, image.id)

    def test_persists_across_restarts_without_requiring_profiles_json(self):
        collection = self.catalog.create_collection("Saved")
        image = self.add_image()
        self.catalog.add_image_to_collection(collection.id, image.id)
        with WallpaperCatalog(self.db_path) as another:
            self.assertEqual(another.list_collection_images(collection.id), [image])

    def test_existing_json_preferences_are_never_rewritten(self):
        profile = self.root / "database" / "profiles.json"
        legacy = {"version": 1, "monitors": {"display-a": {"mode": "folder"}}}
        original = json.dumps(legacy, indent=2).encode("utf-8")
        profile.write_bytes(original)
        self.catalog.create_collection("New collection")
        self.catalog.create_tag("New tag")
        self.assertEqual(profile.read_bytes(), original)

    def test_rejects_incompatible_future_schema(self):
        future_path = self.root / "future.sqlite3"
        # sqlite3's context manager commits/rolls back but does NOT close.
        # Closing explicitly is essential before TemporaryDirectory cleanup
        # on Windows, where an open SQLite file cannot be unlinked.
        with closing(sqlite3.connect(future_path)) as connection:
            connection.execute("PRAGMA user_version = 999")
            connection.commit()
        with self.assertRaisesRegex(ValueError, "newer than supported"):
            WallpaperCatalog(future_path)

    def test_invalid_dimensions_do_not_create_records(self):
        with self.assertRaises(ValueError):
            self.catalog.upsert_image(self.root / "x.jpg", width=0, height=1,
                                      size_bytes=12, modified_ns=1)
        with self.assertRaises(ValueError):
            self.catalog.upsert_image(self.root / "x.jpg", width=True, height=1,
                                      size_bytes=12, modified_ns=1)


if __name__ == "__main__":
    unittest.main()
