"""C5 regression tests: strict constraints, preferences and durable history."""

from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

if "wallpaper_orchestrator.platforms.windows" not in sys.modules:
    package = types.ModuleType("wallpaper_orchestrator.platforms")
    package.__path__ = []
    adapter = types.ModuleType("wallpaper_orchestrator.platforms.windows")
    adapter.discover_monitors = lambda: []
    adapter.get_monitor_wallpaper = lambda _mid: None
    adapter.set_monitor_wallpaper = lambda _mid, _path: None
    sys.modules["wallpaper_orchestrator.platforms"] = package
    sys.modules["wallpaper_orchestrator.platforms.windows"] = adapter

from wallpaper_orchestrator import engine
from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.intelligence import (
    ImageRule, MonitorGroup, SelectionPolicy, SmartCollection,
    load_selection_policy, load_smart_collections,
    save_selection_policy, save_smart_collections,
)
from wallpaper_orchestrator.recent_history import RecentHistory, path_key


class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.catalog = WallpaperCatalog()
        self.addCleanup(self.catalog.close)

    def add_image(self, collection, name, size=(1200, 800), tags=()):
        path = self.root / name
        Image.new("RGB", size).save(path)
        record = self.catalog.upsert_image(path, width=size[0], height=size[1],
                                           size_bytes=path.stat().st_size,
                                           modified_ns=path.stat().st_mtime_ns)
        self.catalog.add_image_to_collection(collection.id, record.id)
        for tag in tags:
            self.catalog.tag_image(record.id, tag.id)
        return record

    def plan(self, profiles, monitors, policy, *, recent=None, current=None):
        with patch.object(engine, "discover_monitors", return_value=monitors), \
             patch.object(engine, "get_monitor_wallpaper",
                          side_effect=lambda mid: (current or {}).get(mid)), \
             patch.object(engine, "_theme_limits", return_value={}):
            return engine.create_profile_plan(
                None, profiles, selected_device_ids=set(profiles),
                selection_policy=policy, recent_by_monitor=recent,
            )[0]

    @staticmethod
    def profile():
        return {"mode": "collections", "order": "next"}

    def test_policy_and_smart_settings_survive_roundtrip(self):
        group = MonitorGroup("g", "Pair", ("p", "l"),
                             ImageRule("prefer", include_tags=("winter",)),
                             tag_limits={"winter": 1})
        policy = SelectionPolicy(recent_window=5, recent_mode="require", recent_scope="all",
                                 collection_limits={"art": 1}, groups=(group,))
        save_selection_policy(policy)
        self.assertEqual(load_selection_policy(), policy)
        smart = SmartCollection("c", ("winter",), ("dark",))
        save_smart_collections({"c": smart})
        self.assertEqual(load_smart_collections(), {"c": smart})

    def test_corrupt_policy_not_overwritten(self):
        path = self.root / "bad.json"
        path.write_text('{"version":999,"policy":{}}')
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            save_selection_policy(SelectionPolicy(), path)
        self.assertIn('999', path.read_text())

    def test_history_bounded_and_stable(self):
        with RecentHistory() as history:
            for i in range(105):
                history.record({"display": self.root / f"image{i}.jpg"})
            values = history.recent("display", 100)
            self.assertEqual(len(values), 100)
            self.assertEqual(values[0], path_key(self.root / "image104.jpg"))
            self.assertNotIn(path_key(self.root / "image0.jpg"), values)
        with RecentHistory() as history:
            self.assertEqual(history.recent("display", 2), values[:2])

    def test_global_history_counts_multi_display_apply_as_one_event(self):
        with RecentHistory() as history:
            with patch("wallpaper_orchestrator.recent_history.time.time", return_value=1000):
                history.record({"p": "one.jpg", "l": "two.jpg"})
            with patch("wallpaper_orchestrator.recent_history.time.time", return_value=1100):
                history.record({"p": "three.jpg"})
            self.assertEqual(set(history.recent_global_events(1)), {path_key("three.jpg")})
            self.assertEqual(set(history.recent_global_events(2)),
                             {path_key("one.jpg"), path_key("two.jpg"), path_key("three.jpg")})

    def test_global_recent_require_blocks_image_used_on_another_monitor(self):
        collection = self.catalog.create_collection("All landscapes")
        previously_used = self.add_image(collection, "a.png")
        unused = self.add_image(collection, "b.png")
        self.catalog.set_monitor_collections("l", [collection.id])
        with RecentHistory() as history:
            history.record({"another-landscape": previously_used.path})
        monitors = [SimpleNamespace(device_id="l", orientation="landscape")]
        policy = SelectionPolicy(recent_window=1, recent_mode="require", recent_scope="all")
        self.assertEqual(self.plan({"l": self.profile()}, monitors, policy)["l"].path,
                         unused.path)
        self.assertEqual(self.plan({"l": self.profile()}, monitors,
                                   replace(policy, recent_scope="monitor"))["l"].path,
                         previously_used.path)

    def test_smart_collection_is_virtual_and_follows_tag_assignments(self):
        physical = self.catalog.create_collection("Physical")
        smart = self.catalog.create_collection("Winter Photos")
        winter = self.catalog.create_tag("Winter")
        dark = self.catalog.create_tag("Dark")
        included = self.add_image(physical, "snow.png", tags=(winter,))
        excluded = self.add_image(physical, "dark.png", tags=(winter, dark))
        save_smart_collections({smart.id: SmartCollection(smart.id, (winter.id,), (dark.id,))})
        self.assertEqual([r.id for r in self.catalog.list_collection_images(smart.id)],
                         [included.id])
        self.assertIn(smart.id, [c.id for c in self.catalog.list_image_collections(included.id)])
        self.catalog.tag_image(included.id, dark.id)
        self.assertEqual(self.catalog.list_collection_images(smart.id), [])
        self.assertTrue((self.root / "snow.png").exists())
        self.assertTrue((self.root / "dark.png").exists())

    def test_require_tag_and_prefer_tag_differ(self):
        c = self.catalog.create_collection("Art")
        winter = self.catalog.create_tag("Winter")
        first = self.add_image(c, "a.png")
        tagged = self.add_image(c, "z.png", tags=(winter,))
        self.catalog.set_monitor_collections("l", [c.id])
        monitors = [SimpleNamespace(device_id="l", orientation="landscape")]
        required = SelectionPolicy(monitor_rules={"l": ImageRule("require", include_tags=(winter.id,))})
        self.assertEqual(self.plan({"l": self.profile()}, monitors, required)["l"].path, tagged.path)
        preferred = replace(required, monitor_rules={"l": ImageRule("prefer", include_tags=(winter.id,))})
        self.assertEqual(self.plan({"l": self.profile()}, monitors, preferred)["l"].path, tagged.path)
        self.catalog.untag_image(tagged.id, winter.id)
        with self.assertRaisesRegex(ValueError, "Require"):
            self.plan({"l": self.profile()}, monitors, required)
        self.assertEqual(self.plan({"l": self.profile()}, monitors, preferred)["l"].path, first.path)

    def test_recent_require_and_prefer(self):
        c = self.catalog.create_collection("Art")
        a = self.add_image(c, "a.png")
        b = self.add_image(c, "b.png")
        self.catalog.set_monitor_collections("l", [c.id])
        monitors = [SimpleNamespace(device_id="l", orientation="landscape")]
        recent = {"l": (path_key(a.path),)}
        policy = SelectionPolicy(recent_window=5, recent_mode="require")
        self.assertEqual(self.plan({"l": self.profile()}, monitors, policy, recent=recent)["l"].path, b.path)
        with self.assertRaisesRegex(ValueError, "recent-image window|last 5"):
            self.plan({"l": self.profile()}, monitors, policy,
                      recent={"l": (path_key(a.path), path_key(b.path))})
        self.assertIn(self.plan({"l": self.profile()}, monitors, replace(policy, recent_mode="prefer"),
                                recent={"l": (path_key(a.path), path_key(b.path))})["l"].path,
                      (a.path, b.path))

    def test_global_collection_limit_three_monitors(self):
        art = self.catalog.create_collection("Catholic Art")
        other = self.catalog.create_collection("Nature")
        portrait = self.add_image(art, "p.png", (800, 1200))
        self.add_image(art, "art-l.png")
        safe = self.add_image(other, "nature.png")
        self.catalog.set_monitor_collections("p", [art.id])
        self.catalog.set_monitor_collections("l", [art.id, other.id])
        monitors = [SimpleNamespace(device_id="p", orientation="portrait"),
                    SimpleNamespace(device_id="l", orientation="landscape")]
        policy = SelectionPolicy(collection_limits={art.id: 1})
        result = self.plan({"p": self.profile(), "l": self.profile()}, monitors, policy)
        self.assertEqual(result["p"].path, portrait.path)
        self.assertEqual(result["l"].path, safe.path)

    def test_group_limits_and_strict_tags(self):
        c = self.catalog.create_collection("Mixed")
        winter = self.catalog.create_tag("Winter")
        safe = self.add_image(c, "p-safe.png", (800, 1200), tags=(winter,))
        self.add_image(c, "p-plain.png", (800, 1200))
        self.add_image(c, "l-winter.png", tags=(winter,))
        plain = self.add_image(c, "l-plain.png")
        self.catalog.set_monitor_collections("p", [c.id])
        self.catalog.set_monitor_collections("l", [c.id])
        group = MonitorGroup("g", "Both", ("p", "l"),
                             ImageRule("prefer", include_tags=(winter.id,)),
                             tag_limits={winter.id: 1})
        monitors = [SimpleNamespace(device_id="p", orientation="portrait"),
                    SimpleNamespace(device_id="l", orientation="landscape")]
        result = self.plan({"p": self.profile(), "l": self.profile()}, monitors,
                           SelectionPolicy(groups=(group,)))
        self.assertEqual(len(result), 2)
        self.assertEqual(sum(bool({t.id for t in self.catalog.list_image_tags(
            self.catalog.find_image_by_path(w.path).id)} & {winter.id})
            for w in result.values()), 1)

    def test_joint_search_avoids_recent_wallpaper_on_both_displays(self):
        first = self.catalog.create_collection("First source")
        second = self.catalog.create_collection("Second source")
        shared = self.add_image(first, "aa.png")
        alternative = self.add_image(first, "bb.png")
        recently_used = self.add_image(second, "cc.png")
        self.catalog.add_image_to_collection(second.id, shared.id)
        self.catalog.set_monitor_collections("a", [first.id])
        self.catalog.set_monitor_collections("b", [second.id])
        monitors = [SimpleNamespace(device_id="a", orientation="landscape"),
                    SimpleNamespace(device_id="b", orientation="landscape")]
        result = self.plan({"a": self.profile(), "b": self.profile()}, monitors,
                           SelectionPolicy(recent_mode="prefer"),
                           recent={"b": (path_key(recently_used.path),)})
        self.assertEqual(result["a"].path, alternative.path)
        self.assertEqual(result["b"].path, shared.path)


if __name__ == "__main__":
    unittest.main()
