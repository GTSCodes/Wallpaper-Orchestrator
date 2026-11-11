"""C3 interval storage and timing, independent of Windows and Qt."""

from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wallpaper_orchestrator.automation import (
    AutomationConfig, IntervalRule, advance_intervals, due_intervals,
    load_automation, matching_rule, record_interval_results, remove_interval,
    resolve_interval_targets, save_automation, set_interval,
)
from wallpaper_orchestrator import background
from wallpaper_orchestrator.background import (
    agent_alive, agent_health_text, diagnostic_lines, startup_command,
)


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "automation.json"

    def test_interval_starts_after_chosen_delay(self):
        config = set_interval(AutomationConfig(), ["portrait"], 15 * 60, now=1000)
        self.assertEqual(due_intervals(config, 1899), [])
        self.assertEqual(len(due_intervals(config, 1900)), 1)

    def test_catchup_advances_once_not_repeatedly(self):
        config = set_interval(AutomationConfig(), ["landscape"], 60, now=100)
        selected = {config.rules[0].id}
        self.assertEqual(len(due_intervals(config, 10000)), 1)
        config = advance_intervals(config, selected, 10000, "Applied")
        self.assertEqual(due_intervals(config, 10000), [])
        self.assertEqual(due_intervals(config, 10060), list(config.rules))

    def test_selected_group_is_order_independent(self):
        config = set_interval(AutomationConfig(), ["landscape", "portrait"], 600, now=50)
        original_id = config.rules[0].id
        config = set_interval(config, ["portrait", "landscape"], 3600, now=75)
        self.assertEqual(len(config.rules), 1)
        self.assertEqual(config.rules[0].id, original_id)
        self.assertEqual(config.rules[0].interval_seconds, 3600)

    def test_distinct_groups_are_independent(self):
        config = set_interval(AutomationConfig(), ["portrait"], 60, now=0)
        config = set_interval(config, ["landscape"], 120, now=0)
        self.assertEqual(len(config.rules), 2)
        self.assertEqual(len(due_intervals(config, 61)), 1)
        self.assertEqual(len(due_intervals(config, 121)), 2)

    def test_remove_only_exact_group(self):
        config = set_interval(AutomationConfig(), ["portrait"], 60, now=0)
        config = set_interval(config, ["portrait", "landscape"], 90, now=0)
        config = remove_interval(config, ["landscape", "portrait"])
        self.assertEqual(len(config.rules), 1)
        self.assertEqual(config.rules[0].monitor_ids, ("portrait",))

    def test_group_waits_until_every_selected_monitor_is_connected(self):
        config = set_interval(AutomationConfig(), ["p", "l"], 60, now=0)
        targets = resolve_interval_targets(
            list(config.rules), {"p"}, {"p": {"mode": "collections"}}
        )
        self.assertEqual(targets.device_ids, set())
        self.assertEqual(targets.deferred_ids, {config.rules[0].id})

    def test_fixed_member_is_never_shuffled(self):
        config = set_interval(AutomationConfig(), ["p", "l"], 60, now=0)
        targets = resolve_interval_targets(
            list(config.rules), {"p", "l"},
            {"p": {"mode": "fixed"}, "l": {"mode": "collections"}},
        )
        self.assertEqual(targets.device_ids, {"l"})
        self.assertEqual(targets.attempted_ids, {config.rules[0].id})

    def test_persistence_and_background_setting(self):
        config = set_interval(AutomationConfig(background_enabled=True), ["p"], 180, now=5)
        save_automation(config, self.path)
        self.assertEqual(load_automation(self.path), config)

    def test_global_pause_survives_roundtrip_and_interval_updates(self):
        config = set_interval(AutomationConfig(background_enabled=True, paused=True),
                              ["portrait"], 60, now=5)
        save_automation(config, self.path)
        self.assertTrue(load_automation(self.path).paused)
        self.assertEqual(load_automation(self.path).rules, config.rules)
        changed = set_interval(load_automation(self.path), ["landscape"], 120, now=5)
        self.assertTrue(changed.paused)

    def test_legacy_automation_file_defaults_to_not_paused(self):
        self.path.write_text('{"version": 1, "background_enabled": true, "rules": []}',
                             encoding="utf-8")
        self.assertFalse(load_automation(self.path).paused)

    def test_pause_prevents_due_checks_without_qt_and_keeps_intervals(self):
        config = set_interval(AutomationConfig(background_enabled=True, paused=True),
                              ["portrait"], 60, now=5)
        with patch.object(background, "load_automation", return_value=config):
            self.assertEqual(background._apply_due_automations(),
                             "Automations paused by user.")
        self.assertEqual(config.rules[0].next_due_utc, 65)

    def test_invalid_pause_setting_preserves_file(self):
        invalid = '{"version": 1, "background_enabled": true, "rules": [], "paused": 1}'
        self.path.write_text(invalid, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "pause setting"):
            save_automation(AutomationConfig(), self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), invalid)

    def test_manual_result_does_not_move_next_due(self):
        config = set_interval(AutomationConfig(), ["p"], 90, now=50)
        due = config.rules[0].next_due_utc
        updated = record_interval_results(config, {config.rules[0].id}, "Failed")
        self.assertEqual(updated.rules[0].next_due_utc, due)
        self.assertEqual(updated.rules[0].last_result, "Failed")

    def test_invalid_future_schema_never_overwritten(self):
        text = '{"version": 7, "background_enabled": true, "rules": []}'
        self.path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unsupported automation"):
            save_automation(AutomationConfig(), self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), text)

    def test_invalid_interval_or_duplicate_monitors_rejected(self):
        with self.assertRaisesRegex(ValueError, "Interval"):
            set_interval(AutomationConfig(), ["p"], 59, now=0)
        with self.assertRaisesRegex(ValueError, "distinct monitor"):
            IntervalRule("a", ("p", "p"), 60, 60)

    def test_inactive_rules_not_due(self):
        config = set_interval(AutomationConfig(), ["p"], 60, now=0)
        config = replace(config, rules=(replace(config.rules[0], enabled=False),))
        self.assertEqual(due_intervals(config, 1000), [])

    def test_agent_liveness_requires_fresh_heartbeat(self):
        with patch("wallpaper_orchestrator.background.read_status",
                   return_value={"running": True, "heartbeat_utc": 500}):
            self.assertTrue(agent_alive(now=525))
            self.assertFalse(agent_alive(now=600))

    def test_agent_health_makes_silent_failures_visible(self):
        with patch.object(background, "read_status", return_value={
            "running": False, "heartbeat_utc": 500, "message": "COM failed"
        }):
            self.assertIn("Not responding", agent_health_text(now=600))
            self.assertIn("COM failed", agent_health_text(now=600))

    def test_diagnostics_show_rule_and_due_time(self):
        config = set_interval(AutomationConfig(background_enabled=True), ["p"], 60, now=100)
        with patch.object(background, "load_automation", return_value=config), \
             patch.object(background, "read_status", return_value={}):
            report = "\n".join(diagnostic_lines(now=161))
        self.assertIn("Background enabled: True", report)
        self.assertIn("Saved interval rules: 1", report)
        self.assertIn("due now", report)

    def test_gui_fallback_uses_same_mutex_as_agent(self):
        closed = []
        kernel = SimpleNamespace(CloseHandle=lambda handle: closed.append(handle.value))
        with patch.object(background, "_require_windows"), \
             patch.object(background, "agent_alive", return_value=False), \
             patch.object(background, "_single_instance_mutex", return_value=567), \
             patch.object(background, "_apply_due_automations", return_value="Applied 2"), \
             patch.object(background.ctypes, "windll", SimpleNamespace(kernel32=kernel), create=True):
            self.assertEqual(background.apply_due_in_foreground_if_agent_missing(),
                             (True, "Applied 2"))
        self.assertEqual(closed, [567])

    def test_gui_fallback_defers_to_existing_agent(self):
        with patch.object(background, "_require_windows"), \
             patch.object(background, "agent_alive", return_value=True), \
             patch.object(background, "_single_instance_mutex") as mutex:
            self.assertEqual(background.apply_due_in_foreground_if_agent_missing(),
                             (False, None))
            mutex.assert_not_called()

    def test_scanner_failure_does_not_stop_due_wallpaper_update(self):
        with patch.object(background, "_scan_changed_folders",
                          side_effect=AttributeError("missing catalogue method")), \
             patch.object(background, "_apply_due_automations",
                          return_value="Applied 2 wallpaper(s)") as apply:
            outcome = background._run_cycle(object(), set())
        apply.assert_called_once_with()
        self.assertIn("Applied 2 wallpaper(s)", outcome)
        self.assertIn("Library auto-scan failed", outcome)

    def test_background_scanner_needs_only_catalog_listing_api(self):
        class LegacyCatalogue:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def list_collections(self):
                return [SimpleNamespace(id="c")]

            def list_folder_links(self, _collection_id):
                return [SimpleNamespace(id="link")]

        detector = SimpleNamespace(poll=lambda _links: {"link"})
        with patch.object(background, "WallpaperCatalog", return_value=LegacyCatalogue()), \
             patch.object(background, "scan_folder",
                          return_value=SimpleNamespace(added=2)) as scan:
            message = background._scan_changed_folders(detector, set())
        scan.assert_called_once()
        self.assertIn("2 new image(s)", message)
        with patch("wallpaper_orchestrator.background.read_status",
                   return_value={"running": False, "heartbeat_utc": 600}):
            self.assertFalse(agent_alive(now=600))

    def test_startup_installation_rejected_outside_windows(self):
        with patch("wallpaper_orchestrator.background.sys.platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "requires Windows"):
                startup_command()


if __name__ == "__main__":
    unittest.main()
