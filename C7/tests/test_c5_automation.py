"""Seasonal transition, persistence and automation conflict regressions."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from wallpaper_orchestrator.automation import IntervalRule
from wallpaper_orchestrator.automation_management import find_overlaps, inventory
from wallpaper_orchestrator.calendar_rules import create_calendar_rule, load_calendar_rules
from wallpaper_orchestrator.scheduler import new_rule
from wallpaper_orchestrator.seasonal_automation import (
    SeasonState, evaluate_seasons, load_seasonal_state, save_seasonal_state,
)
from wallpaper_orchestrator import background


UTC = timezone.utc


def season(**kwargs):
    return create_calendar_rule(
        "November", period="months", months=(11,), include_tag_ids=("winter",),
        **kwargs,
    )


class SeasonAutomationTests(unittest.TestCase):
    def test_start_end_and_periodic_single_selected_monitor(self):
        rule = season(target_ids=("portrait",), apply_on_start=True,
                      apply_on_end=True, shuffle_interval_seconds=60)
        november = datetime(2026, 11, 11, 12, tzinfo=UTC)
        start = evaluate_seasons([rule], {"portrait", "landscape"}, november,
                                 epoch=1000, previous={})
        self.assertEqual(start.target_ids, {"portrait"})
        steady = evaluate_seasons([rule], {"portrait", "landscape"}, november,
                                  epoch=1030, previous=start.new_states)
        self.assertFalse(steady.target_ids)
        interval = evaluate_seasons([rule], {"portrait", "landscape"}, november,
                                    epoch=1061, previous=steady.new_states)
        self.assertEqual(interval.target_ids, {"portrait"})
        end = evaluate_seasons([rule], {"portrait", "landscape"},
                               datetime(2026, 12, 1, 0, tzinfo=UTC),
                               epoch=2000, previous=interval.new_states)
        self.assertEqual(end.target_ids, {"portrait"})
        after = evaluate_seasons([rule], {"portrait", "landscape"},
                                 datetime(2026, 12, 1, 0, tzinfo=UTC),
                                 epoch=2005, previous=end.new_states)
        self.assertFalse(after.target_ids)

    def test_passive_legacy_rules_do_not_trigger(self):
        now = datetime(2026, 11, 11, 13, tzinfo=UTC)
        passive = season()
        result = evaluate_seasons([passive], {"p"}, now, epoch=100, previous={})
        self.assertFalse(result.target_ids)
        self.assertEqual(result.new_states["p"].rule_id, passive.id)

    def test_overlapping_seasons_coalesce_by_effective_priority(self):
        now = datetime(2026, 11, 11, 13, tzinfo=UTC)
        first = season(priority=10, apply_on_start=True, apply_on_end=True)
        second = season(priority=90, apply_on_start=True, apply_on_end=True)
        state = evaluate_seasons([first, second], {"p"}, now, epoch=100, previous={})
        self.assertEqual(state.target_ids, {"p"})
        self.assertEqual(state.new_states["p"].rule_id, second.id)
        demoted = evaluate_seasons([first, replace(second, enabled=False)],
                                   {"p"}, now, epoch=130, previous=state.new_states)
        self.assertEqual(demoted.target_ids, {"p"})
        self.assertEqual(demoted.new_states["p"].rule_id, first.id)

    def test_disconnected_monitor_state_preserved(self):
        now = datetime(2026, 11, 11, 12, tzinfo=UTC)
        prior = {"offline": SeasonState("old-rule", True, 10)}
        state = evaluate_seasons([season()], {"online"}, now,
                                 epoch=100, previous=prior)
        self.assertEqual(state.new_states["offline"], prior["offline"])

    def test_state_roundtrip_and_future_version_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "seasonal.json"
            state = {"p": SeasonState("id", True, 123.0)}
            save_seasonal_state(state, path)
            self.assertEqual(load_seasonal_state(path), state)
            path.write_text('{"version": 42, "monitors": {}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                save_seasonal_state(state, path)
            self.assertIn("42", path.read_text())

    def test_old_calendar_state_fields_default_passive(self):
        old = season()
        self.assertFalse(old.apply_on_start)
        self.assertFalse(old.apply_on_end)
        self.assertEqual(old.shuffle_interval_seconds, 0)
        with self.assertRaisesRegex(ValueError, "interval"):
            replace(old, shuffle_interval_seconds=30)

    def test_pre_c5_seasonal_json_loads_without_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rules.json"
            legacy = season().to_dict()
            for field in ("apply_on_start", "apply_on_end", "shuffle_interval_seconds"):
                del legacy[field]
            path.write_text(json.dumps({"version": 1, "rules": [legacy]}), encoding="utf-8")
            loaded = load_calendar_rules(path)
            self.assertEqual(len(loaded), 1)
            self.assertFalse(loaded[0].apply_on_start)
            self.assertEqual(loaded[0].shuffle_interval_seconds, 0)

    def test_automation_inventory_differentiates_conflicts(self):
        now = datetime(2026, 11, 11, 12, tzinfo=UTC)
        intervals = [
            IntervalRule("i1", ("p",), 60, 1000),
            IntervalRule("i2", ("p", "l"), 120, 1000),
        ]
        daily = [new_rule("Daily", "12:00", target="monitor", device_id="p", now=now)]
        seasons = [season(target_ids=("p",), priority=5),
                   season(target_ids=("p",), priority=10)]
        overlaps = find_overlaps(inventory(intervals, daily, seasons),
                                 {"p", "l"}, now=now, seasons=seasons)
        self.assertTrue(any(o.first.kind == "Interval" and o.second.kind == "Interval"
                            and o.severity == "warning" for o in overlaps))
        self.assertTrue(any(o.first.kind == "Seasonal" and o.second.kind == "Seasonal"
                            and o.severity == "warning" for o in overlaps))
        self.assertTrue(any(o.severity == "info" for o in overlaps))

    def test_background_coalesces_seasonal_only_trigger_and_targets_one_monitor(self):
        """A season alone can trigger the agent, even without an interval rule."""
        from wallpaper_orchestrator.automation import AutomationConfig
        from wallpaper_orchestrator import engine
        date = datetime.now().astimezone()
        rule = create_calendar_rule("Current season", period="months",
                                    months=(date.month,), include_tag_ids=("tag",),
                                    target_ids=("p",), apply_on_start=True)
        py_side = types.ModuleType("PySide6")
        qt_core = types.ModuleType("PySide6.QtCore")
        qt_core.QSettings = lambda *_a: SimpleNamespace(value=lambda _key, default, **_kw: default)
        with patch.dict(sys.modules, {"PySide6": py_side, "PySide6.QtCore": qt_core}), \
             patch.object(background, "load_automation", return_value=AutomationConfig()), \
             patch.object(background, "load_schedules", return_value=[]), \
             patch.object(background, "load_calendar_rules", return_value=[rule]), \
             patch.object(background, "load_seasonal_state", return_value={}), \
             patch.object(background, "save_seasonal_state") as stamp, \
             patch.object(background, "load_profiles", return_value={"p": {"mode": "collections"},
                                                                  "l": {"mode": "collections"}}), \
             patch.object(background, "save_profiles"), \
             patch("wallpaper_orchestrator.platforms.windows.discover_monitors",
                   return_value=[SimpleNamespace(device_id="p"),
                                 SimpleNamespace(device_id="l")]), \
             patch.object(engine, "create_profile_plan", return_value=(
                 {"p": SimpleNamespace(path=Path("changed.jpg"))}, {"p": "p"})) as planner, \
             patch.object(engine, "apply_plan") as apply:
            outcome = background._apply_due_automations()
        self.assertIn("Applied 1 wallpaper", outcome)
        self.assertEqual(planner.call_args.kwargs["selected_device_ids"], {"p"})
        apply.assert_called_once()
        stamp.assert_called_once()


if __name__ == "__main__":
    unittest.main()
