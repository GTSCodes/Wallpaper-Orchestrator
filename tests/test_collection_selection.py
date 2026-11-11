"""C1 engine tests with a simulated platform adapter (no Windows COM needed)."""

import os
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

# The actual app imports the Windows COM adapter. Tests supply only the three
# required adapter functions before importing the engine on non-Windows hosts.
if "wallpaper_orchestrator.platforms.windows" not in sys.modules:
    package = types.ModuleType("wallpaper_orchestrator.platforms")
    package.__path__ = []
    adapter = types.ModuleType("wallpaper_orchestrator.platforms.windows")
    adapter.discover_monitors = lambda: []
    adapter.get_monitor_wallpaper = lambda _device_id: None
    adapter.set_monitor_wallpaper = lambda _device_id, _path: None
    sys.modules["wallpaper_orchestrator.platforms"] = package
    sys.modules["wallpaper_orchestrator.platforms.windows"] = adapter

from wallpaper_orchestrator import engine
from wallpaper_orchestrator.calendar_rules import create_calendar_rule, save_calendar_rules
from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.profiles import default_profile, load_profiles, save_profiles


class CollectionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env_patch = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.catalog = WallpaperCatalog()
        self.addCleanup(self.catalog.close)

    def add_image(self, collection_id, name, size):
        folder = self.root / ("images-" + collection_id)
        folder.mkdir(exist_ok=True)
        path = folder / name
        with Image.new("RGB", size, "blue") as image:
            image.save(path)
        links = self.catalog.list_folder_links(collection_id)
        link = links[0] if links else self.catalog.link_folder(collection_id, folder)
        record = self.catalog.upsert_image(
            path, width=size[0], height=size[1],
            size_bytes=path.stat().st_size, modified_ns=path.stat().st_mtime_ns,
        )
        self.catalog.register_discovered_image(link.id, record.id)
        return record

    @staticmethod
    def profile(mode="collections"):
        profile = default_profile()
        profile["mode"] = mode
        return profile

    def test_random_order_does_not_reuse_current_if_alternative_exists(self):
        current = SimpleNamespace(path=self.root / "current.jpg")
        other = SimpleNamespace(path=self.root / "other.jpg")
        options = [current, other]
        with patch.object(engine, "get_monitor_wallpaper", return_value=str(current.path)), \
             patch.object(engine.random, "shuffle"):
            actual = engine._order_candidates(options, None, {"order": "random"}, "p")
        self.assertEqual(actual[0], other)
        self.assertEqual(actual[1], current)

    def test_random_order_survives_windows_current_wallpaper_query_failure(self):
        current = SimpleNamespace(path=self.root / "current.jpg")
        other = SimpleNamespace(path=self.root / "other.jpg")
        with patch.object(engine, "get_monitor_wallpaper", side_effect=OSError("COM unavailable")), \
             patch.object(engine.random, "shuffle"):
            ordered = engine._order_candidates(
                [current, other], None,
                {"order": "random", "last_image": str(current.path)}, "p",
            )
        self.assertEqual(ordered[0], other)

    def test_calendar_winter_tag_filters_existing_collection_images(self):
        collection = self.catalog.create_collection("Nature")
        winter = self.add_image(collection.id, "snow.jpg", (1400, 900))
        self.add_image(collection.id, "summer.jpg", (1400, 900))
        tag = self.catalog.create_tag("Winter")
        self.catalog.tag_image(winter.id, tag.id)
        self.catalog.set_monitor_collections("l", [collection.id])
        rule = create_calendar_rule("December Winter", include_tag_ids=(tag.id,))
        now = datetime(2026, 12, 5, 12, tzinfo=timezone.utc)
        with patch.object(engine, "discover_monitors", return_value=[
            SimpleNamespace(device_id="l", orientation="landscape")
        ]), patch.object(engine, "get_monitor_wallpaper", return_value=None):
            assignments, _ = engine.create_profile_plan(
                None, {"l": self.profile()}, selected_device_ids={"l"},
                calendar_rules=[rule], calendar_now=now,
                respect_theme_limits=False,
            )
        self.assertEqual(assignments["l"].path, winter.path)

    def test_calendar_prefer_falls_back_but_require_explains(self):
        collection = self.catalog.create_collection("Art")
        normal = self.add_image(collection.id, "ordinary.jpg", (1400, 900))
        self.catalog.set_monitor_collections("l", [collection.id])
        rule = create_calendar_rule("Christmas", include_tag_ids=("missing-tag",))
        now = datetime(2026, 12, 5, 12, tzinfo=timezone.utc)
        monitor = SimpleNamespace(device_id="l", orientation="landscape")
        with patch.object(engine, "discover_monitors", return_value=[monitor]), \
             patch.object(engine, "get_monitor_wallpaper", return_value=None):
            chosen, _ = engine.create_profile_plan(
                None, {"l": self.profile()}, selected_device_ids={"l"},
                calendar_rules=[rule], calendar_now=now, respect_theme_limits=False,
            )
            self.assertEqual(chosen["l"].path, normal.path)
            with self.assertRaisesRegex(ValueError, "Seasonal rule 'Christmas'"):
                engine.create_profile_plan(
                    None, {"l": self.profile()}, selected_device_ids={"l"},
                    calendar_rules=[replace(rule, behavior="require")],
                    calendar_now=now, respect_theme_limits=False,
                )

    def test_calendar_cannot_expand_monitor_subscription(self):
        assigned = self.catalog.create_collection("Assigned")
        other = self.catalog.create_collection("Other")
        retained = self.add_image(assigned.id, "assigned.jpg", (1400, 900))
        elsewhere = self.add_image(other.id, "other.jpg", (1400, 900))
        self.catalog.set_monitor_collections("l", [assigned.id])
        rule = create_calendar_rule("Other collection", collection_ids=(other.id,))
        now = datetime(2026, 12, 5, 12, tzinfo=timezone.utc)
        monitor = SimpleNamespace(device_id="l", orientation="landscape")
        with patch.object(engine, "discover_monitors", return_value=[monitor]), \
             patch.object(engine, "get_monitor_wallpaper", return_value=None):
            chosen, _ = engine.create_profile_plan(
                None, {"l": self.profile()}, selected_device_ids={"l"},
                calendar_rules=[rule], calendar_now=now, respect_theme_limits=False,
            )
        self.assertEqual(chosen["l"].path, retained.path)
        self.assertNotEqual(chosen["l"].path, elsewhere.path)

    def test_background_and_gui_plans_read_saved_calendar_automatically(self):
        collection = self.catalog.create_collection("December")
        winter = self.add_image(collection.id, "winter.jpg", (1400, 900))
        self.add_image(collection.id, "other.jpg", (1400, 900))
        tag = self.catalog.create_tag("Christmas")
        self.catalog.tag_image(winter.id, tag.id)
        self.catalog.set_monitor_collections("l", [collection.id])
        save_calendar_rules([create_calendar_rule(
            "Christmas", include_tag_ids=(tag.id,), months=(12,),
        )])
        now = datetime(2026, 12, 5, 12, tzinfo=timezone.utc)
        with patch.object(engine, "discover_monitors", return_value=[
            SimpleNamespace(device_id="l", orientation="landscape")
        ]), patch.object(engine, "get_monitor_wallpaper", return_value=None):
            assignments, _ = engine.create_profile_plan(
                None, {"l": self.profile()}, selected_device_ids={"l"},
                calendar_now=now, respect_theme_limits=False,
            )
        self.assertEqual(assignments["l"].path, winter.path)

    def test_calendar_exclusion_never_chooses_excluded_tag(self):
        collection = self.catalog.create_collection("Landscape")
        excluded = self.add_image(collection.id, "dark.jpg", (1400, 900))
        safe = self.add_image(collection.id, "bright.jpg", (1400, 900))
        tag = self.catalog.create_tag("Dark")
        self.catalog.tag_image(excluded.id, tag.id)
        self.catalog.set_monitor_collections("l", [collection.id])
        rule = create_calendar_rule("No dark in December", exclude_tag_ids=(tag.id,))
        with patch.object(engine, "discover_monitors", return_value=[
            SimpleNamespace(device_id="l", orientation="landscape")
        ]), patch.object(engine, "get_monitor_wallpaper", return_value=None):
            assignments, _ = engine.create_profile_plan(
                None, {"l": self.profile()}, selected_device_ids={"l"},
                calendar_rules=[rule],
                calendar_now=datetime(2026, 12, 5, tzinfo=timezone.utc),
                respect_theme_limits=False,
            )
        self.assertEqual(assignments["l"].path, safe.path)

    def test_one_monitor_selects_its_collection_image(self):
        collection = self.catalog.create_collection("Nature")
        image = self.add_image(collection.id, "nature.png", (900, 1600))
        self.catalog.set_monitor_collections("portrait-id", [collection.id])
        monitors = [SimpleNamespace(device_id="portrait-id", orientation="portrait")]
        with patch.object(engine, "discover_monitors", return_value=monitors), \
             patch.object(engine, "get_monitor_wallpaper", return_value=None), \
             patch.object(engine, "_theme_limits", return_value={}):
            assignments, _ = engine.create_profile_plan(
                None, {"portrait-id": self.profile()},
                selected_device_ids={"portrait-id"},
            )
        self.assertEqual(assignments["portrait-id"].path, image.path)
        self.assertIn("Nature", assignments["portrait-id"].themes)

    def test_theme_limit_respected_between_collections(self):
        catholic = self.catalog.create_collection("Catholic Art")
        marvel = self.catalog.create_collection("Marvel")
        portrait = self.add_image(catholic.id, "portrait.png", (800, 1200))
        self.add_image(catholic.id, "catholic_landscape.png", (1200, 800))
        landscape = self.add_image(marvel.id, "marvel.png", (1200, 800))
        self.catalog.set_monitor_collections("p", [catholic.id])
        self.catalog.set_monitor_collections("l", [catholic.id, marvel.id])
        monitors = [SimpleNamespace(device_id="p", orientation="portrait"),
                    SimpleNamespace(device_id="l", orientation="landscape")]
        with patch.object(engine, "discover_monitors", return_value=monitors), \
             patch.object(engine, "get_monitor_wallpaper", return_value=None), \
             patch.object(engine, "_theme_limits", return_value={"Catholic Art": 1}):
            assignments, _ = engine.create_profile_plan(
                None, {"p": self.profile(), "l": self.profile()},
                selected_device_ids={"p", "l"},
            )
        self.assertEqual(assignments["p"].path, portrait.path)
        self.assertEqual(assignments["l"].path, landscape.path)

    def test_explicit_tag_counts_against_limit(self):
        c = self.catalog.create_collection("Artwork")
        p = self.add_image(c.id, "dark_p.png", (800, 1200))
        self.add_image(c.id, "dark_l.png", (1200, 800))
        normal = self.add_image(c.id, "normal_l.png", (1400, 900))
        dark = self.catalog.create_tag("Dark")
        for img in self.catalog.list_collection_images(c.id):
            if img.id != normal.id:
                self.catalog.tag_image(img.id, dark.id)
        self.catalog.set_monitor_collections("p", [c.id])
        self.catalog.set_monitor_collections("l", [c.id])
        monitors = [SimpleNamespace(device_id="p", orientation="portrait"),
                    SimpleNamespace(device_id="l", orientation="landscape")]
        with patch.object(engine, "discover_monitors", return_value=monitors), \
             patch.object(engine, "get_monitor_wallpaper", return_value=None), \
             patch.object(engine, "_theme_limits", return_value={"Dark": 1}):
            assignments, _ = engine.create_profile_plan(
                None, {"p": self.profile(), "l": self.profile()},
                selected_device_ids={"p", "l"},
            )
        self.assertEqual(assignments["p"].path, p.path)
        self.assertEqual(assignments["l"].path, normal.path)

    def test_missing_collection_assignment_is_reported(self):
        monitors = [SimpleNamespace(device_id="p", orientation="portrait")]
        with patch.object(engine, "discover_monitors", return_value=monitors), \
             patch.object(engine, "_theme_limits", return_value={}):
            with self.assertRaisesRegex(ValueError, "no collections assigned"):
                engine.create_profile_plan(
                    None, {"p": self.profile()}, selected_device_ids={"p"}
                )

    def test_unchanged_fixed_monitor_consumes_theme_capacity(self):
        catholic = self.catalog.create_collection("Catholic Art")
        marvel = self.catalog.create_collection("Marvel")
        fixed = self.add_image(catholic.id, "fixed.png", (800, 1200))
        self.add_image(catholic.id, "candidate_catholic.png", (1200, 800))
        safe = self.add_image(marvel.id, "candidate_marvel.png", (1200, 800))
        self.catalog.set_monitor_collections("landscape", [catholic.id, marvel.id])
        fixed_profile = self.profile("fixed")
        fixed_profile["image"] = str(fixed.path)
        monitors = [SimpleNamespace(device_id="portrait", orientation="portrait"),
                    SimpleNamespace(device_id="landscape", orientation="landscape")]
        with patch.object(engine, "discover_monitors", return_value=monitors), \
             patch.object(engine, "get_monitor_wallpaper",
                          side_effect=lambda m: str(fixed.path) if m == "portrait" else None), \
             patch.object(engine, "_theme_limits", return_value={"Catholic Art": 1}):
            assignments, _ = engine.create_profile_plan(
                None, {"portrait": fixed_profile,
                       "landscape": self.profile()},
                selected_device_ids={"landscape"},
            )
        self.assertEqual(assignments["landscape"].path, safe.path)

    def test_collections_and_legacy_folder_modes_can_mix(self):
        collection = self.catalog.create_collection("Art")
        chosen = self.add_image(collection.id, "portrait.png", (800, 1200))
        self.catalog.set_monitor_collections("portrait", [collection.id])
        folder = self.root / "landscape-legacy"
        folder.mkdir()
        with Image.new("RGB", (1200, 800)) as image:
            image.save(folder / "old.jpg")
        legacy = self.profile("folder")
        legacy["folder"] = str(folder)
        monitors = [SimpleNamespace(device_id="portrait", orientation="portrait"),
                    SimpleNamespace(device_id="landscape", orientation="landscape")]
        with patch.object(engine, "discover_monitors", return_value=monitors), \
             patch.object(engine, "get_monitor_wallpaper", return_value=None), \
             patch.object(engine, "_theme_limits", return_value={}):
            assignments, _ = engine.create_profile_plan(
                None, {"portrait": self.profile(), "landscape": legacy},
                selected_device_ids={"portrait", "landscape"},
            )
        self.assertEqual(assignments["portrait"].path, chosen.path)
        self.assertEqual(assignments["landscape"].path, folder / "old.jpg")

    def test_existing_legacy_folder_mode_still_works(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        with Image.new("RGB", (1200, 800)) as image:
            image.save(legacy / "old.png")
        profile = self.profile("folder")
        profile["folder"] = str(legacy)
        monitors = [SimpleNamespace(device_id="l", orientation="landscape")]
        with patch.object(engine, "discover_monitors", return_value=monitors), \
             patch.object(engine, "get_monitor_wallpaper", return_value=None), \
             patch.object(engine, "_theme_limits", return_value={}):
            assignments, _ = engine.create_profile_plan(
                None, {"l": profile}, selected_device_ids={"l"}
            )
        self.assertEqual(assignments["l"].path, legacy / "old.png")

    def test_profile_schema_accepts_collection_mode_and_old_modes(self):
        profiles = {"p": self.profile("collections"),
                    "l": self.profile("folder")}
        save_profiles(profiles)
        loaded = load_profiles()
        self.assertEqual(loaded["p"]["mode"], "collections")
        self.assertEqual(loaded["l"]["mode"], "folder")

    def test_catholic_landscape_is_allowed_with_limits_disabled(self):
        """A collection is not hardwired to portrait or landscape monitors."""
        catholic = self.catalog.create_collection("Catholic Art")
        portrait = self.add_image(catholic.id, "transfiguration.png", (800, 1200))
        dispute = self.add_image(catholic.id, "disputa.png", (3258, 2349))
        self.catalog.set_monitor_collections("landscape", [catholic.id])
        monitors = [SimpleNamespace(device_id="portrait", orientation="portrait"),
                    SimpleNamespace(device_id="landscape", orientation="landscape")]
        with patch.object(engine, "discover_monitors", return_value=monitors), \
             patch.object(engine, "get_monitor_wallpaper",
                          side_effect=lambda dev: str(portrait.path) if dev == "portrait" else None), \
             patch.object(engine, "_theme_limits",
                          side_effect=AssertionError("limits must be opt-in")), \
             patch.object(engine, "load_wallpapers",
                          side_effect=AssertionError("collection shuffle must not rescan folders")):
            assignments, _ = engine.create_profile_plan(
                self.root, {"landscape": self.profile()},
                selected_device_ids={"landscape"},
                respect_theme_limits=False,
            )
        self.assertEqual(assignments["landscape"].path, dispute.path)

    def test_conflicting_limit_explains_theme_and_occupied_monitor(self):
        catholic = self.catalog.create_collection("Catholic Art")
        portrait = self.add_image(catholic.id, "transfiguration.png", (800, 1200))
        self.add_image(catholic.id, "disputa.png", (1200, 800))
        self.catalog.set_monitor_collections("landscape", [catholic.id])
        monitors = [SimpleNamespace(device_id="portrait", orientation="portrait"),
                    SimpleNamespace(device_id="landscape", orientation="landscape")]
        with patch.object(engine, "discover_monitors", return_value=monitors), \
             patch.object(engine, "get_monitor_wallpaper",
                          side_effect=lambda dev: str(portrait.path) if dev == "portrait" else None), \
             patch.object(engine, "_theme_limits", return_value={"Catholic Art": 1}):
            with self.assertRaisesRegex(ValueError, "Catholic Art") as caught:
                engine.create_profile_plan(
                    None, {"landscape": self.profile()},
                    selected_device_ids={"landscape"},
                )
        self.assertIn("portrait display", str(caught.exception))
        self.assertIn("Cross-monitor limits", str(caught.exception))

    def test_missing_portrait_orientation_names_collection_and_counts(self):
        miles = self.catalog.create_collection("Miles Morales")
        self.add_image(miles.id, "landscape.png", (1400, 900))
        self.catalog.set_monitor_collections("portrait", [miles.id])
        monitors = [SimpleNamespace(device_id="portrait", orientation="portrait")]
        with patch.object(engine, "discover_monitors", return_value=monitors), \
             patch.object(engine, "_theme_limits", return_value={}):
            with self.assertRaisesRegex(ValueError, "No portrait wallpapers") as caught:
                engine.create_profile_plan(
                    None, {"portrait": self.profile()},
                    selected_device_ids={"portrait"},
                )
        self.assertIn("'Miles Morales': 0 portrait image(s)", str(caught.exception))
        self.assertIn("1 available total", str(caught.exception))

    def test_no_unique_images_error_explains_overlap(self):
        both = self.catalog.create_collection("Shared")
        self.add_image(both.id, "single.png", (1200, 800))
        self.catalog.set_monitor_collections("first", [both.id])
        self.catalog.set_monitor_collections("second", [both.id])
        monitors = [SimpleNamespace(device_id="first", orientation="landscape"),
                    SimpleNamespace(device_id="second", orientation="landscape")]
        with patch.object(engine, "discover_monitors", return_value=monitors), \
             patch.object(engine, "get_monitor_wallpaper", return_value=None), \
             patch.object(engine, "_theme_limits", return_value={}):
            with self.assertRaisesRegex(ValueError, "Only 1 distinct") as caught:
                engine.create_profile_plan(
                    None, {"first": self.profile(), "second": self.profile()},
                    selected_device_ids={"first", "second"},
                )
        self.assertIn("2 selected displays", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
