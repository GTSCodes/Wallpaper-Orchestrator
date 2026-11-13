"""C8.2.2: immutable baseline, startup defaults and stale-agent repair."""

from dataclasses import replace
import json
import os
from pathlib import Path, PureWindowsPath
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wallpaper_orchestrator import background, desktop_entry
from wallpaper_orchestrator.automation import AutomationConfig, load_automation, save_automation
from wallpaper_orchestrator.catalog import SCHEMA_VERSION, WallpaperCatalog
from wallpaper_orchestrator.onboarding_state import (
    default_background_for_setup, should_offer_setup,
)
from wallpaper_orchestrator.restore_points import (
    capture_first_restore_point, capture_wallpapers,
    first_restore_point_path, read_first_restore_point, read_restore_point,
    restore_point_path, save_restore_point,
)


class FirstRestorePointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        context = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)})
        context.start()
        self.addCleanup(context.stop)

    def test_automatic_first_capture_is_write_once_not_custom(self):
        monitors = Mock(return_value=[SimpleNamespace(device_id="portrait"),
                                      SimpleNamespace(device_id="landscape")])
        get = Mock(side_effect=lambda mid: f"C:/{mid}.jpg")
        self.assertTrue(capture_first_restore_point(monitors, get))
        self.assertFalse(capture_first_restore_point(monitors, get))
        self.assertEqual(monitors.call_count, 1)
        self.assertEqual(read_first_restore_point(), {
            "portrait": "C:/portrait.jpg", "landscape": "C:/landscape.jpg"
        })
        self.assertIsNone(read_restore_point())
        self.assertNotEqual(first_restore_point_path(), restore_point_path())

    def test_custom_snapshot_may_change_but_first_remains_immutable(self):
        capture_first_restore_point(
            lambda: [SimpleNamespace(device_id="one")], lambda _: "initial.jpg"
        )
        self.assertTrue(save_restore_point({"one": "custom.jpg"}))
        self.assertTrue(save_restore_point({"one": "new-custom.jpg"}, overwrite=True))
        self.assertEqual(read_restore_point(), {"one": "new-custom.jpg"})
        self.assertEqual(read_first_restore_point(), {"one": "initial.jpg"})

    def test_invalid_existing_first_point_is_preserved_and_blocks_capture(self):
        path = first_restore_point_path()
        path.parent.mkdir(parents=True)
        path.write_text("damaged", encoding="utf-8")
        with self.assertRaises(ValueError):
            capture_first_restore_point(lambda: [], lambda _: None)
        self.assertEqual(path.read_text(encoding="utf-8"), "damaged")

    def test_failed_discovery_does_not_publish_partial_baseline(self):
        def discover():
            return [SimpleNamespace(device_id="one"), SimpleNamespace(device_id="two")]
        def get(mid):
            if mid == "two":
                raise OSError("monitor disconnected")
            return "first.jpg"
        with self.assertRaises(OSError):
            capture_first_restore_point(discover, get)
        self.assertFalse(first_restore_point_path().exists())

    def test_existing_legacy_restore_point_is_not_assumed_to_be_original(self):
        self.assertTrue(save_restore_point({"one": "old-manual.jpg"}))
        self.assertTrue(capture_first_restore_point(
            lambda: [SimpleNamespace(device_id="one")], lambda _: "current.jpg"
        ))
        self.assertEqual(read_restore_point(), {"one": "old-manual.jpg"})
        self.assertEqual(read_first_restore_point(), {"one": "current.jpg"})


class BackgroundDefaultTests(unittest.TestCase):
    def test_new_install_default_is_true_without_enabling_interval_rules(self):
        self.assertTrue(default_background_for_setup(AutomationConfig(),
                                                    saved_preference_exists=False))
        self.assertEqual(AutomationConfig().rules, ())

    def test_user_opt_out_and_safety_pause_are_not_overridden(self):
        self.assertFalse(default_background_for_setup(
            AutomationConfig(background_enabled=False), saved_preference_exists=True))
        self.assertFalse(default_background_for_setup(
            AutomationConfig(background_enabled=False, paused=True),
            saved_preference_exists=True))

    def test_reopened_setup_keeps_explicit_preference(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            config = AutomationConfig(background_enabled=False, paused=True)
            save_automation(config)
            self.assertFalse(default_background_for_setup(
                load_automation(), saved_preference_exists=True
            ))
            self.assertTrue(load_automation().paused)


class FreshInstallSetupTests(unittest.TestCase):
    def test_first_run_eligibility_must_be_captured_before_library_initialises(self):
        # A new LibraryPanel creates catalog.sqlite3 while loading its collection
        # list. Without the early decision, the initial setup wizard never opens.
        class FreshSettings:
            def value(self, key, default=None, **kwargs):
                return default

            def allKeys(self):
                return []

        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            settings = FreshSettings()
            offer_at_launch = should_offer_setup(settings)
            self.assertTrue(offer_at_launch)
            with WallpaperCatalog():
                pass  # Mimics LibraryPanel.refresh_collections().
            self.assertFalse(should_offer_setup(settings))
            # The cached initial decision must still permit setup.
            self.assertTrue(offer_at_launch)

    def test_gui_caches_new_user_decision_before_library_widget(self):
        # Structural check avoids requiring PySide6/Windows COM in pure tests.
        gui_source = (Path(__file__).resolve().parents[1] / "src" /
                      "wallpaper_orchestrator" / "gui.py").read_text(encoding="utf-8")
        self.assertLess(
            gui_source.index("self._offer_first_run_setup = should_offer_setup(self.settings)"),
            gui_source.index("self.library_panel = LibraryPanel(self)"),
        )
        self.assertIn("if self._offer_first_run_setup:", gui_source)


class AgentCompatibilityTests(unittest.TestCase):
    def test_status_identifies_catalogue_schema_and_current_executable(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"LOCALAPPDATA": directory}), \
             patch.object(background, "_native_process_executable",
                          return_value=Path("C:/New/WallpaperOrchestrator.exe")):
            background._write_status("okay")
            result = json.loads(background.status_path().read_text(encoding="utf-8"))
            self.assertEqual(result["catalogue_schema_version"], SCHEMA_VERSION)
            self.assertEqual(
                PureWindowsPath(result["agent_executable"]),
                PureWindowsPath("C:/New/WallpaperOrchestrator.exe"),
            )

    def test_matching_agent_is_not_restarted(self):
        status = {"running": True, "heartbeat_utc": 100, "pid": 4,
                  "agent_executable": r"C:\Apps\WallpaperOrchestrator.exe",
                  "catalogue_schema_version": SCHEMA_VERSION}
        with patch.object(background, "agent_alive", return_value=True), \
             patch.object(background, "read_status", return_value=status), \
             patch.object(background, "agent_launch_arguments",
                          return_value=[r"c:\apps\WallpaperOrchestrator.exe", "--background-agent"]), \
             patch.object(background, "install_startup") as install, \
             patch.object(background, "start_agent") as start, \
             patch.object(background, "stop_agent") as stop, \
             patch.object(background, "load_automation",
                          return_value=AutomationConfig(background_enabled=True)):
            background.ensure_current_agent()
        install.assert_called_once()
        start.assert_not_called()
        stop.assert_not_called()

    def test_old_agent_is_stopped_and_replaced_without_database_downgrade(self):
        with patch.object(background, "agent_alive", side_effect=[True, True, False]), \
             patch.object(background, "agent_is_current", return_value=False), \
             patch.object(background, "stop_agent", return_value=True) as stop, \
             patch.object(background, "install_startup") as install, \
             patch.object(background, "start_agent") as launch, \
             patch.object(background, "load_automation",
                          return_value=AutomationConfig(background_enabled=True)):
            background.ensure_current_agent()
        stop.assert_called_once()
        install.assert_called_once()
        launch.assert_called_once()

    def test_failure_to_stop_old_agent_prevents_second_launch(self):
        with patch.object(background, "agent_alive", return_value=True), \
             patch.object(background, "agent_is_current", return_value=False), \
             patch.object(background, "stop_agent", return_value=False), \
             patch.object(background, "install_startup") as install, \
             patch.object(background, "start_agent") as launch, \
             patch.object(background, "load_automation",
                          return_value=AutomationConfig(background_enabled=True)):
            with self.assertRaisesRegex(RuntimeError, "outdated background agent"):
                background.ensure_current_agent()
        install.assert_not_called()
        launch.assert_not_called()

    def test_restore_pause_never_restarts_agent_or_registers_startup(self):
        for config in (AutomationConfig(background_enabled=False, paused=True),
                       AutomationConfig(background_enabled=True, paused=True),
                       AutomationConfig(background_enabled=False, paused=False)):
            with self.subTest(config=config), \
                 patch.object(background, "load_automation", return_value=config), \
                 patch.object(background, "install_startup") as install, \
                 patch.object(background, "start_agent") as launch:
                background.ensure_current_agent()
                install.assert_not_called()
                launch.assert_not_called()

    def test_legacy_status_is_rejected_even_if_heartbeat_is_recent(self):
        with patch.object(background, "agent_alive", return_value=True), \
             patch.object(background, "agent_launch_arguments",
                          return_value=["C:/Current/WallpaperOrchestrator.exe", "--background-agent"]):
            self.assertFalse(background.agent_is_current(status={
                "running": True, "heartbeat_utc": 123, "pid": 66,
                "message": "Catalogue version 3 is newer than supported version 2"
            }))


if __name__ == "__main__":
    unittest.main()
