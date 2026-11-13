"""C8 regression coverage for the source/bundle boundary and installer."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from wallpaper_orchestrator import background, desktop_entry
from wallpaper_orchestrator.onboarding_state import should_offer_setup


class RuntimeModeTests(unittest.TestCase):
    def test_bundle_marker_survives_renamed_executable(self):
        exe = r"C:\Program Files\Wallpaper Orchestrator\Renamed Launcher.exe"
        with patch.object(background, "IS_STANDALONE_BUNDLE", True), \
             patch.object(background, "_native_process_executable", return_value=Path(exe)), \
             patch.object(background.sys, "executable", "Z:/nonexistent/python.exe"):
            self.assertEqual(background.agent_launch_arguments(),
                             [exe, "--background-agent"])

    def test_nuitka_uses_real_executable_if_sys_executable_is_missing_python(self):
        exe = Path(r"C:\Users\Example\Wallpaper Orchestrator\WallpaperOrchestrator.exe")
        with patch.object(background, "IS_STANDALONE_BUNDLE", True), \
             patch.object(background, "_native_process_executable", return_value=exe), \
             patch.object(background.sys, "executable", r"C:\Fake\dist\python.exe"):
            self.assertEqual(background.agent_launch_arguments(),
                             [str(exe), "--background-agent"])

    def test_native_windows_module_image_api_returns_application(self):
        exe = r"C:\Programs With Spaces\WallpaperOrchestrator.exe"
        def fake_get_name(_module, buffer, _capacity):
            buffer.value = exe
            return len(exe)
        get_name = MagicMock(side_effect=fake_get_name)
        kernel = types.SimpleNamespace(GetModuleFileNameW=get_name)
        with patch.object(background.sys, "platform", "win32"), \
             patch.object(background.ctypes, "WinDLL", return_value=kernel, create=True):
            self.assertEqual(background._native_process_executable(), Path(exe))
        get_name.assert_called_once()

    def test_source_launcher_still_uses_pythonw(self):
        script = Path(r"C:\Dev Folder\src\wallpaper_orchestrator\background.py")
        with patch.object(background, "IS_STANDALONE_BUNDLE", False), \
             patch.object(background, "_pythonw_path", return_value=Path("pythonw.exe")), \
             patch.object(background.sys, "executable", "python.exe"):
            self.assertEqual(background.agent_launch_arguments(script=script),
                             ["pythonw.exe", str(script)])

    def test_nuitka_compiled_flag_still_supported(self):
        with patch.dict(background.__dict__, {"__compiled__": object()}), \
             patch.object(background, "IS_STANDALONE_BUNDLE", False), \
             patch.object(background, "_native_process_executable", return_value=Path(r"C:\App\Custom.exe")), \
             patch.object(background.sys, "executable", "Z:/nonexistent/python.exe"):
            self.assertEqual(background.agent_launch_arguments(),
                             [r"C:\App\Custom.exe", "--background-agent"])

    def test_startup_quote_spaces_in_binary_path(self):
        exe = Path(r"C:\Users\Example\App Folder\WallpaperOrchestrator.exe")
        expected = subprocess.list2cmdline([str(exe), "--background-agent"])
        with patch.object(background, "_require_windows"), \
             patch.object(background, "agent_launch_arguments",
                          return_value=[str(exe), "--background-agent"]):
            self.assertEqual(background.startup_command(), expected)
            self.assertTrue(expected.startswith('"'))

    def test_start_agent_launches_exact_compiled_argv_and_records_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "App Folder" / "WallpaperOrchestrator.exe"
            exe.parent.mkdir()
            exe.touch()
            fake_child = types.SimpleNamespace(pid=1234)
            with patch.object(background, "_require_windows"), \
                 patch.object(background, "agent_launch_arguments",
                              return_value=[str(exe), "--background-agent"]), \
                 patch.object(background.subprocess, "DETACHED_PROCESS", 8, create=True), \
                 patch.object(background.subprocess, "CREATE_NEW_PROCESS_GROUP", 512,
                              create=True), \
                 patch.object(background.subprocess, "Popen",
                              return_value=fake_child) as spawn, \
                 patch.object(background, "_record_launch_attempt") as log:
                background.start_agent()
            self.assertEqual(spawn.call_args.args[0], [str(exe), "--background-agent"])
            # Windows may expose the temp folder as an 8.3 short path or as
            # its long name. Verify the same directory, not the spelling.
            self.assertTrue(Path(spawn.call_args.kwargs["cwd"]).samefile(exe.parent))
            log.assert_called_once()
            self.assertIn("created child PID 1234", log.call_args.kwargs["result"])

    def test_nonexistent_agent_exe_reports_usable_diagnostic(self):
        with patch.object(background, "_require_windows"), \
             patch.object(background, "agent_launch_arguments",
                          return_value=["Z:/no such exe.exe", "--background-agent"]), \
             patch.object(background, "_record_launch_attempt") as log:
            with self.assertRaisesRegex(FileNotFoundError, "does not exist"):
                background.start_agent()
            self.assertEqual(log.call_args.kwargs["result"], "missing executable")

    def test_winerror2_is_diagnosed_with_subprocess_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "WallpaperOrchestrator.exe"
            exe.touch()
            with patch.object(background, "_require_windows"), \
                 patch.object(background, "agent_launch_arguments",
                              return_value=[str(exe), "--background-agent"]), \
                 patch.object(background.subprocess, "DETACHED_PROCESS", 8, create=True), \
                 patch.object(background.subprocess, "CREATE_NEW_PROCESS_GROUP", 512,
                              create=True), \
                 patch.object(background.subprocess, "Popen",
                              side_effect=FileNotFoundError(2, "The system cannot find the file specified")), \
                 patch.object(background, "_record_launch_attempt") as log:
                with self.assertRaisesRegex(RuntimeError, "See .*background-launch.log"):
                    background.start_agent()
            self.assertEqual(log.call_args.kwargs["result"], "process creation failed")

    def test_startup_registry_write_and_uninstall(self):
        entries = {}
        class Key:
            def __enter__(self): return self
            def __exit__(self, *_): return None
        fake = types.ModuleType("winreg")
        fake.HKEY_CURRENT_USER = 1
        fake.KEY_SET_VALUE = 2
        fake.REG_SZ = 1
        fake.CreateKeyEx = MagicMock(return_value=Key())
        fake.OpenKey = MagicMock(return_value=Key())
        fake.SetValueEx = MagicMock(side_effect=lambda _, name, _reserved,
                                    _kind, data: entries.__setitem__(name, data))
        fake.DeleteValue = MagicMock(side_effect=lambda _, name: entries.pop(name))
        with patch.dict(sys.modules, {"winreg": fake}), \
             patch.object(background, "_require_windows"), \
             patch.object(background, "startup_command", return_value='"C:\\Path With Spaces\\App.exe" --background-agent'):
            background.install_startup()
            self.assertEqual(entries[background.STARTUP_NAME],
                             '"C:\\Path With Spaces\\App.exe" --background-agent')
            background.uninstall_startup()
        self.assertNotIn(background.STARTUP_NAME, entries)

    def test_repair_startup_only_if_already_opted_in(self):
        from wallpaper_orchestrator.automation import AutomationConfig
        with patch("wallpaper_orchestrator.automation.load_automation",
                   return_value=AutomationConfig(background_enabled=False)), \
             patch.object(background, "ensure_current_agent") as repair, \
             patch.object(background, "uninstall_startup") as uninstall, \
             patch.object(background, "stop_agent", return_value=True) as stop:
            self.assertEqual(desktop_entry.main(["--repair-startup"]), 0)
            repair.assert_not_called()
            uninstall.assert_called_once()
            stop.assert_called_once()
        with patch("wallpaper_orchestrator.automation.load_automation",
                   return_value=AutomationConfig(background_enabled=True)), \
             patch.object(background, "ensure_current_agent") as repair:
            self.assertEqual(desktop_entry.main(["--repair-startup"]), 0)
            repair.assert_called_once()
        with patch("wallpaper_orchestrator.automation.load_automation",
                   return_value=AutomationConfig(background_enabled=True, paused=True)), \
             patch.object(background, "ensure_current_agent") as repair, \
             patch.object(background, "uninstall_startup") as uninstall, \
             patch.object(background, "stop_agent", return_value=True) as stop:
            self.assertEqual(desktop_entry.main(["--repair-startup"]), 0)
            repair.assert_not_called()
            uninstall.assert_called_once()
            stop.assert_called_once()

    def test_failed_self_test_writes_diagnostic_instead_of_silent_exit(self):
        with patch.object(desktop_entry, "_self_test", side_effect=RuntimeError("missing executable")), \
             patch.object(desktop_entry, "_record_startup_failure") as report:
            self.assertEqual(desktop_entry.main(["--self-test"]), 1)
            report.assert_called_once()
            self.assertEqual(report.call_args.args[0], "self-test")

    def test_stop_agent_command_and_dispatch(self):
        with patch.object(background, "stop_agent", return_value=True) as stop:
            self.assertEqual(desktop_entry.main(["--stop-agent"]), 0)
            stop.assert_called_once()
        with patch.object(background, "stop_agent", return_value=False):
            self.assertEqual(desktop_entry.main(["--stop-agent"]), 1)

    def test_stale_agent_heartbeat_not_treated_as_running(self):
        with patch.object(background, "read_status", return_value={
            "running": True, "heartbeat_utc": 10, "pid": 1234,
        }):
            self.assertFalse(background.agent_alive(now=55))
            self.assertTrue(background.agent_alive(now=20))


class InstallerSetupTests(unittest.TestCase):
    class Settings:
        def __init__(self, keys=(), finished=False):
            self.keys, self.finished = list(keys), finished
        def value(self, _key, _default, *, type): return self.finished
        def allKeys(self): return self.keys

    def test_first_run_only_on_empty_settings_and_no_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(should_offer_setup(self.Settings(), app_data=root))
            self.assertFalse(should_offer_setup(self.Settings(["wallpaper_library"]),
                                                app_data=root))
            self.assertFalse(should_offer_setup(self.Settings(finished=True), app_data=root))
            (root / "profiles.json").write_text("{}")
            self.assertFalse(should_offer_setup(self.Settings(), app_data=root))

    def test_build_and_installer_files_present(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "desktop.py").is_file())
        self.assertTrue((root / "README.md").is_file())
        self.assertTrue((root / "installer/WallpaperOrchestrator.iss").is_file())
        build = (root / "scripts/build_windows.ps1").read_text()
        self.assertIn("IS_STANDALONE_BUNDLE = True", build)
        self.assertIn("$originalIdentity", build)
        iss = (root / "installer/WallpaperOrchestrator.iss").read_text()
        self.assertIn("PrivilegesRequired=lowest", iss)
        self.assertIn("--stop-agent", iss)
        self.assertIn("--repair-startup", iss)
        self.assertNotIn("deletefiles", iss.lower())


if __name__ == "__main__":
    unittest.main()
