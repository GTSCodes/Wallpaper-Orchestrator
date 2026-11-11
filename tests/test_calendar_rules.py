"""C4 calendar evaluation and persistence without a GUI or Windows COM."""

from dataclasses import replace
from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

from wallpaper_orchestrator.calendar_rules import (
    active_calendar_rule, create_calendar_rule, load_calendar_rules,
    matches_calendar, save_calendar_rules,
)


def at(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class CalendarRuleTests(unittest.TestCase):
    def rule(self, **kwargs):
        return create_calendar_rule("Winter", include_tag_ids=("winter-id",), **kwargs)

    def test_december_rule_only_matches_december(self):
        rule = self.rule(months=(12,))
        self.assertTrue(matches_calendar(rule, at(2026, 12, 8), "portrait"))
        self.assertFalse(matches_calendar(rule, at(2026, 11, 8), "portrait"))

    def test_date_range_across_new_year_is_inclusive(self):
        rule = self.rule(period="range", months=(), start_date="12-01", end_date="01-06")
        self.assertTrue(matches_calendar(rule, at(2026, 12, 1), "m"))
        self.assertTrue(matches_calendar(rule, at(2027, 1, 6), "m"))
        self.assertFalse(matches_calendar(rule, at(2027, 1, 7), "m"))

    def test_leap_day_allowed_but_not_fabricated(self):
        rule = self.rule(period="range", months=(), start_date="02-29", end_date="02-29")
        self.assertTrue(matches_calendar(rule, at(2028, 2, 29), "m"))
        self.assertFalse(matches_calendar(rule, at(2027, 2, 28), "m"))

    def test_overnight_time_and_weekday_anchor(self):
        # Monday evening through early Tuesday counts as Monday.
        rule = self.rule(months=(12,), weekdays=(0,), all_day=False,
                         start_time="22:00", end_time="02:00")
        self.assertTrue(matches_calendar(rule, at(2026, 12, 7, 23), "m"))
        self.assertTrue(matches_calendar(rule, at(2026, 12, 8, 1), "m"))
        self.assertFalse(matches_calendar(rule, at(2026, 12, 8, 3), "m"))

    def test_over_year_overnight_uses_start_month(self):
        rule = self.rule(months=(12,), all_day=False,
                         start_time="22:00", end_time="02:00")
        self.assertTrue(matches_calendar(rule, at(2027, 1, 1, 1), "m"))

    def test_group_target_does_not_apply_to_other_displays(self):
        rule = self.rule(months=(12,), target_ids=("portrait", "landscape"))
        self.assertTrue(matches_calendar(rule, at(2026, 12, 2), "portrait"))
        self.assertFalse(matches_calendar(rule, at(2026, 12, 2), "other"))

    def test_higher_priority_wins_and_specific_beats_global_tie(self):
        global_rule = self.rule(months=(12,), priority=50)
        targeted = self.rule(months=(12,), priority=50, target_ids=("p",))
        high = self.rule(months=(12,), priority=90)
        self.assertEqual(active_calendar_rule([global_rule, targeted], at(2026, 12, 2), "p"), targeted)
        self.assertEqual(active_calendar_rule([global_rule, targeted, high], at(2026, 12, 2), "p"), high)

    def test_disabled_rules_are_ignored(self):
        rule = replace(self.rule(), enabled=False)
        self.assertIsNone(active_calendar_rule([rule], at(2026, 12, 2), "p"))

    def test_reject_invalid_conflicting_filters(self):
        with self.assertRaisesRegex(ValueError, "include and exclude"):
            self.rule(exclude_tag_ids=("winter-id",))
        with self.assertRaisesRegex(ValueError, "Select at least"):
            create_calendar_rule("Empty")
        with self.assertRaisesRegex(ValueError, "start and end"):
            self.rule(all_day=False, start_time="08:00", end_time="08:00")

    def test_requires_aware_datetime(self):
        with self.assertRaisesRegex(ValueError, "aware"):
            matches_calendar(self.rule(), datetime(2026, 12, 2), "p")

    def test_json_round_trip_and_future_schema_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "calendar_rules.json"
            rule = self.rule(period="range", months=(),
                             start_date="12-01", end_date="01-06", behavior="require")
            save_calendar_rules([rule], path)
            self.assertEqual(load_calendar_rules(path), [rule])
            content = '{"version": 7, "rules": []}'
            path.write_text(content, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                save_calendar_rules([], path)
            self.assertEqual(path.read_text(encoding="utf-8"), content)


if __name__ == "__main__":
    unittest.main()
