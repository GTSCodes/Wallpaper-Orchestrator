"""C7 pure tray status decisions, without requiring Qt or Windows."""

import unittest

from wallpaper_orchestrator.automation import AutomationConfig, set_interval
from wallpaper_orchestrator.tray_state import automation_state_text, next_shuffle_text


class TrayStateTests(unittest.TestCase):
    def test_next_interval_respects_due_time(self):
        config = set_interval(AutomationConfig(), ["p"], 90, now=100)
        self.assertEqual(next_shuffle_text(config, now=100), "Next interval: in 2m")
        self.assertEqual(next_shuffle_text(config, now=189), "Next interval: in 1s")
        self.assertEqual(next_shuffle_text(config, now=200), "Next interval: due now")

    def test_paused_state_is_distinct_from_agent_liveness(self):
        config = AutomationConfig(background_enabled=True, paused=True)
        self.assertEqual(next_shuffle_text(config), "Next interval: paused")
        self.assertEqual(automation_state_text(config, agent_running=True),
                         "Automations paused")

    def test_status_reflects_background_and_agent(self):
        enabled = AutomationConfig(background_enabled=True)
        self.assertEqual(automation_state_text(enabled, agent_running=True),
                         "Background running")
        self.assertEqual(automation_state_text(enabled, agent_running=False),
                         "Background not responding")
        self.assertIn("Background off", automation_state_text(
            AutomationConfig(), agent_running=False))

    def test_no_interval_does_not_claim_no_schedules(self):
        self.assertIn("daily schedules may apply", next_shuffle_text(AutomationConfig()))
