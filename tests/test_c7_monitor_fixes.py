"""Headless C7/C8.1 regressions for monitor-aware interval automation."""

import tempfile
import unittest
import sys
import types
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wallpaper_orchestrator.automation import (
    AutomationConfig, IntervalRule, enforce_interval_exclusivity,
    normalize_interval_conflicts, overlapping_intervals,
    load_automation, resolve_interval_targets, save_automation, set_interval,
)
from wallpaper_orchestrator.monitor_labels import monitor_labels


class MonitorFixes(unittest.TestCase):
    def test_laptop_lid_disconnection_does_not_block_external_monitors(self):
        config = set_interval(AutomationConfig(), ["laptop", "left", "right"], 60, now=5)
        result = resolve_interval_targets(list(config.rules), {"left", "right"}, {})
        self.assertEqual(result.device_ids, {"left", "right"})
        self.assertEqual(result.attempted_ids, {config.rules[0].id})
        self.assertFalse(result.deferred_ids)
        reconnected = resolve_interval_targets(list(config.rules), {"laptop", "left", "right"}, {})
        self.assertEqual(reconnected.device_ids, {"laptop", "left", "right"})

    def test_wholly_disconnected_interval_is_deferred_without_stamping(self):
        config = set_interval(AutomationConfig(), ["laptop"], 60, now=5)
        result = resolve_interval_targets(list(config.rules), {"left", "right"}, {})
        self.assertFalse(result.device_ids)
        self.assertFalse(result.attempted_ids)
        self.assertEqual(result.deferred_ids, {config.rules[0].id})

    def test_one_fixed_monitor_never_blocks_other_members(self):
        config = set_interval(AutomationConfig(), ["laptop", "left", "right"], 60, now=5)
        targets = resolve_interval_targets(list(config.rules), {"left", "right"},
                                           {"left": {"mode": "fixed"}})
        self.assertEqual(targets.device_ids, {"right"})

    def test_adding_overlapping_group_pauses_older_without_deleting(self):
        original = set_interval(AutomationConfig(), ["portrait"], 60, now=5)
        first_id = original.rules[0].id
        updated = set_interval(original, ["portrait", "landscape"], 120, now=9)
        self.assertEqual(len(updated.rules), 2)
        self.assertFalse(next(r for r in updated.rules if r.id == first_id).enabled)
        self.assertTrue(next(r for r in updated.rules if r.id != first_id).enabled)
        self.assertEqual(len(overlapping_intervals(updated, ["portrait"])), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "automation.json"
            save_automation(updated, path)
            self.assertEqual(load_automation(path), updated)

    def test_nonoverlapping_groups_can_run_together(self):
        updated = set_interval(AutomationConfig(), ["portrait"], 60, now=5)
        updated = set_interval(updated, ["landscape"], 120, now=9)
        self.assertEqual(sum(r.enabled for r in updated.rules), 2)

    def test_resuming_old_interval_pauses_newer_conflict(self):
        config = set_interval(AutomationConfig(), ["portrait"], 60, now=0)
        config = set_interval(config, ["portrait", "landscape"], 120, now=0)
        old = config.rules[0]
        updated = replace(config, rules=(replace(old, enabled=True), config.rules[1]))
        updated = enforce_interval_exclusivity(updated, old.id)
        self.assertTrue(updated.rules[0].enabled)
        self.assertFalse(updated.rules[1].enabled)

    def test_legacy_overlaps_normalize_deterministically_without_removing(self):
        previous = AutomationConfig(rules=(
            IntervalRule("old", ("p",), 60, 60),
            IntervalRule("new", ("p", "l"), 120, 120),
        ))
        updated = normalize_interval_conflicts(previous)
        self.assertFalse(updated.rules[0].enabled)
        self.assertTrue(updated.rules[1].enabled)
        self.assertEqual(normalize_interval_conflicts(updated), updated)

    def test_agent_updates_two_connected_displays_when_third_is_offline(self):
        """Integration test: physical target set passed to the selection engine."""
        from wallpaper_orchestrator import background, engine
        config = set_interval(
            AutomationConfig(background_enabled=True),
            ["laptop", "left", "right"], 60, now=0,
        )
        qt = types.ModuleType("PySide6")
        qtcore = types.ModuleType("PySide6.QtCore")
        qtcore.QSettings = lambda *_a: SimpleNamespace(
            value=lambda _key, default, **_kw: default
        )
        with patch.dict(sys.modules, {"PySide6": qt, "PySide6.QtCore": qtcore}), \
             patch.object(background, "load_automation", return_value=config), \
             patch.object(background, "save_automation"), \
             patch.object(background, "load_schedules", return_value=[]), \
             patch.object(background, "load_calendar_rules", return_value=[]), \
             patch.object(background, "load_seasonal_state", return_value={}), \
             patch.object(background, "load_profiles", return_value={}), \
             patch.object(background, "save_profiles"), \
             patch("wallpaper_orchestrator.platforms.windows.discover_monitors",
                   return_value=[SimpleNamespace(device_id="left"),
                                 SimpleNamespace(device_id="right")]), \
             patch.object(engine, "create_profile_plan", return_value=(
                 {"left": SimpleNamespace(path=Path("one.jpg")),
                  "right": SimpleNamespace(path=Path("two.jpg"))},
                 {"left": "left", "right": "right"})) as planner, \
             patch.object(engine, "apply_plan") as apply:
            result = background._apply_due_automations()
        self.assertIn("Applied 2 wallpaper", result)
        self.assertEqual(planner.call_args.kwargs["selected_device_ids"],
                         {"left", "right"})
        apply.assert_called_once()

    def test_labels_show_two_different_landscapes_with_stable_ids(self):
        monitors = [
            SimpleNamespace(device_id=r"\\?\DISPLAY#AAA#X&UID1#{guid}", width=1920, height=1080,
                            orientation="landscape"),
            SimpleNamespace(device_id=r"\\?\DISPLAY#BBB#X&UID2#{guid}", width=1920, height=1080,
                            orientation="landscape"),
        ]
        result = monitor_labels(monitors, {"landscape": monitors[0].device_id})
        self.assertEqual(len(set(result.values())), 2)
        self.assertIn("1920×1080", result[monitors[1].device_id])
        self.assertIn("BBB", result[monitors[1].device_id])
        self.assertEqual(result, monitor_labels(list(reversed(monitors)),
                                                {"landscape": monitors[0].device_id}))


if __name__ == "__main__":
    unittest.main()
