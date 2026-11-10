"""C2 scheduling semantics without Windows COM or PySide6."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from wallpaper_orchestrator.scheduler import (
    ScheduleRule, due_rules, load_schedules, new_rule, record_manual_result,
    resolve_targets, save_schedules, stamp_attempts,
)


UK_WINTER = timezone.utc
UK_SUMMER = timezone(timedelta(hours=1))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "schedules.json"

    @staticmethod
    def at(year=2026, month=11, day=10, hour=9, minute=0, tz=UK_WINTER):
        return datetime(year, month, day, hour, minute, tzinfo=tz)

    @staticmethod
    def rule(**fields):
        base = dict(id="r1", name="Morning", time="09:30", target="all",
                    device_id=None)
        base.update(fields)
        return ScheduleRule(**base)

    def test_daily_rule_becomes_due_at_local_time(self):
        rule = self.rule()
        self.assertEqual(due_rules([rule], self.at(hour=9, minute=29)), [])
        self.assertEqual(due_rules([rule], self.at(hour=9, minute=30)), [rule])

    def test_missed_run_catches_up_later_that_day(self):
        rule = self.rule()
        self.assertEqual(due_rules([rule], self.at(hour=18)), [rule])
        stamped = stamp_attempts([rule], {rule.id}, self.at(hour=18), "Applied")
        self.assertEqual(due_rules(stamped, self.at(hour=21)), [])
        self.assertEqual(due_rules(stamped, self.at(day=11, hour=10)), stamped)

    def test_dst_repeated_local_hour_fires_once(self):
        rule = self.rule(time="01:30")
        first = self.at(month=10, day=25, hour=1, minute=30, tz=UK_SUMMER)
        second = self.at(month=10, day=25, hour=1, minute=30, tz=UK_WINTER)
        self.assertEqual(due_rules([rule], first), [rule])
        stamped = stamp_attempts([rule], {rule.id}, first, "Applied")
        self.assertEqual(due_rules(stamped, second), [])

    def test_dst_skipped_local_hour_catches_up(self):
        rule = self.rule(time="01:30")
        after_spring_jump = self.at(month=3, day=29, hour=2, tz=UK_SUMMER)
        self.assertEqual(due_rules([rule], after_spring_jump), [rule])

    def test_weekdays_and_disabled_rules(self):
        today = self.at()  # 2026-11-10 Tuesday.
        mon = self.rule(weekdays=(0,))
        tue = self.rule(id="tuesday", weekdays=(1,))
        off = self.rule(id="disabled", enabled=False)
        self.assertEqual(due_rules([mon, tue, off], today.replace(hour=11)), [tue])

    def test_new_past_time_rule_starts_next_occurrence(self):
        now = self.at(hour=17)
        new = new_rule("Past", "09:00", now=now)
        self.assertEqual(new.last_attempt_date, "2026-11-10")
        self.assertEqual(due_rules([new], now), [])
        self.assertEqual(due_rules([new], self.at(day=11, hour=10)), [new])

    def test_new_future_rule_runs_today(self):
        now = self.at(hour=8)
        new = new_rule("Later", "18:00", now=now)
        self.assertIsNone(new.last_attempt_date)
        self.assertEqual(due_rules([new], self.at(hour=18)), [new])

    def test_manual_run_does_not_consume_automatic_occurrence(self):
        rule = self.rule()
        manual = record_manual_result([rule], {rule.id}, "Manual test succeeded")
        self.assertEqual(manual[0].last_result, "Manual test succeeded")
        self.assertIsNone(manual[0].last_attempt_date)
        self.assertEqual(due_rules(manual, self.at(hour=10)), manual)

    def test_simultaneous_all_and_monitor_rules_merge_targets(self):
        global_rule = self.rule()
        one = self.rule(id="one", target="monitor", device_id="portrait")
        profiles = {
            "portrait": {"mode": "collections", "shuffle_all": False},
            "landscape": {"mode": "collections", "shuffle_all": True},
            "fixed": {"mode": "fixed", "shuffle_all": True},
        }
        targets = resolve_targets(
            [global_rule, one], {"portrait", "landscape", "fixed"}, profiles
        )
        self.assertEqual(targets.device_ids, {"portrait", "landscape"})
        self.assertEqual(targets.attempted_ids, {"r1", "one"})

    def test_offline_monitor_rule_is_deferred(self):
        rule = self.rule(target="monitor", device_id="missing")
        targets = resolve_targets([rule], {"landscape"}, {})
        self.assertEqual(targets.attempted_ids, set())
        self.assertEqual(targets.deferred_ids, {rule.id})

    def test_fixed_monitor_is_not_shuffled(self):
        rule = self.rule(target="monitor", device_id="fixed")
        targets = resolve_targets([rule], {"fixed"}, {"fixed": {"mode": "fixed"}})
        self.assertEqual(targets.device_ids, set())
        self.assertEqual(targets.attempted_ids, {rule.id})

    def test_save_and_load_persist_ids_and_attempt_history(self):
        rules = [self.rule(), self.rule(
            id="custom", name="Evening", time="21:30", target="monitor",
            device_id="monitor-123", weekdays=(0, 2, 4), enabled=False,
            last_attempt_date="2026-11-09", last_result="Applied 1 display",
        )]
        save_schedules(rules, self.path)
        self.assertEqual(load_schedules(self.path), rules)

    def test_reject_corrupt_json_without_rewriting(self):
        self.path.write_text("not json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Cannot read schedules"):
            save_schedules([self.rule()], self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "not json")

    def test_reject_future_schema_without_rewriting(self):
        text = '{"version": 99, "rules": []}'
        self.path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unsupported schedules format"):
            save_schedules([self.rule()], self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), text)

    def test_reject_duplicate_ids_and_invalid_inputs(self):
        with self.assertRaisesRegex(ValueError, "Duplicate schedule"):
            save_schedules([self.rule(), self.rule()], self.path)
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            self.rule(time="25:61")
        with self.assertRaisesRegex(ValueError, "weekdays"):
            self.rule(weekdays=(0, 0))

    def test_rule_edits_can_keep_stable_ids(self):
        existing = self.rule()
        edited = replace(new_rule("Evening", "20:00", now=self.at(hour=10)),
                         id=existing.id)
        self.assertEqual(existing.id, edited.id)
        self.assertEqual(edited.time, "20:00")


if __name__ == "__main__":
    unittest.main()
