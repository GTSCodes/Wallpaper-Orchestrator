"""C3 headless Windows wallpaper agent, launched at user sign-in.

Direct-script bootstrap allows pythonw.exe to run this from any directory,
without depending on an interactive PowerShell PYTHONPATH setting.
"""

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# pythonw.exe has no console, so an import-time failure would otherwise be
# invisible. Install this *before* importing the application modules.
if __name__ == "__main__":
    import sys as _startup_sys

    def _report_unhandled_error(kind, error, traceback):
        try:
            import json as _startup_json
            import os as _startup_os
            import time as _startup_time
            import traceback as _startup_traceback
            from pathlib import Path as _StartupPath

            root = _StartupPath(_startup_os.environ.get("LOCALAPPDATA") or
                                _StartupPath.home() / ".config")
            path = root / "WallpaperOrchestrator" / "background-status.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{_startup_os.getpid()}.tmp")
            temporary.write_text(_startup_json.dumps({
                "pid": _startup_os.getpid(),
                "heartbeat_utc": _startup_time.time(),
                "running": False,
                "message": f"Background agent crashed during startup: {error}",
                "details": "".join(_startup_traceback.format_exception(
                    kind, error, traceback))[-6000:],
            }, indent=2), encoding="utf-8")
            temporary.replace(path)
        except Exception:
            pass  # Never let diagnostics hide the original startup error.

    _startup_sys.excepthook = _report_unhandled_error

import ctypes
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from wallpaper_orchestrator.automation import (
    advance_intervals, automation_path, due_intervals, load_automation,
    record_interval_results, resolve_interval_targets, save_automation,
)
from wallpaper_orchestrator.catalog import WallpaperCatalog
from wallpaper_orchestrator.calendar_rules import load_calendar_rules
from wallpaper_orchestrator.indexer import scan_folder
from wallpaper_orchestrator.profiles import load_profiles, save_profiles
from wallpaper_orchestrator.scheduler import (
    due_rules, load_schedules, resolve_targets, save_schedules, stamp_attempts,
)
from wallpaper_orchestrator.seasonal_automation import (
    SeasonDecision, evaluate_seasons, load_seasonal_state, save_seasonal_state,
)
from wallpaper_orchestrator.watcher import FolderChangeDetector


STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_NAME = "WallpaperOrchestratorAgent"
MUTEX_NAME = r"Local\WallpaperOrchestratorAutomationAgent"


def _require_windows():
    if sys.platform != "win32":
        raise RuntimeError("Background wallpaper application requires Windows.")


def _pythonw_path() -> Path:
    executable = Path(sys.executable)
    silent = executable.with_name("pythonw.exe")
    if not silent.is_file():
        raise RuntimeError(
            "pythonw.exe was not found beside the active Python interpreter. "
            "Launch Wallpaper Orchestrator from your Windows virtual environment."
        )
    return silent


def agent_launch_arguments(
    *, compiled: bool | None = None, executable: Path | None = None,
    script: Path | None = None,
) -> list[str]:
    """Use this app's executable in compiled builds; Python in source builds.

    Nuitka exposes ``__compiled__`` on compiled modules, not ``sys.frozen``.
    This helper is independently testable without launching Windows processes.
    """
    if compiled is None:
        compiled = "__compiled__" in globals()
    if compiled:
        return [str(executable or Path(sys.executable)), "--background-agent"]
    return [str(executable or _pythonw_path()),
            str(script or Path(__file__).resolve())]


def startup_command() -> str:
    """Register one background worker command for source and standalone builds."""
    _require_windows()
    command = subprocess.list2cmdline(agent_launch_arguments())
    if len(command) > 260:
        raise ValueError(
            "The startup path is too long for the Windows Run key. "
            "Move the project to a shorter location before enabling background mode."
        )
    return command


def install_startup() -> None:
    """Opt-in, per-user startup. No elevated privileges or system service."""
    _require_windows()
    import winreg
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, startup_command())


def uninstall_startup() -> None:
    _require_windows()
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, STARTUP_NAME)
    except FileNotFoundError:
        pass


def start_agent() -> None:
    """Launch a separate invisible worker; singleton mutex dedupes."""
    _require_windows()
    subprocess.Popen(
        agent_launch_arguments(),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True,
        creationflags=(subprocess.DETACHED_PROCESS |
                       subprocess.CREATE_NEW_PROCESS_GROUP),
    )


def set_background_enabled(enabled: bool) -> None:
    """Explicit global opt-in shared by interval, daily and seasonal rules."""
    if type(enabled) is not bool:
        raise ValueError("Background preference must be a boolean")
    original = load_automation()
    try:
        if enabled:
            install_startup()
        save_automation(replace(original, background_enabled=enabled))
        if enabled:
            start_agent()  # Singleton mutex prevents duplicate running agents.
        else:
            uninstall_startup()
    except Exception:
        try:
            save_automation(original)
            if not original.background_enabled:
                uninstall_startup()
        except Exception:
            pass
        raise


def status_path() -> Path:
    return automation_path().with_name("background-status.json")


def _write_status(message: str, *, running: bool = True) -> None:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps({
            "pid": os.getpid(), "heartbeat_utc": time.time(),
            "running": running, "message": message,
        }), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_status() -> dict:
    try:
        data = json.loads(status_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def agent_alive(*, now: float | None = None) -> bool:
    status = read_status()
    moment = time.time() if now is None else now
    return (
        status.get("running") is True
        and isinstance(status.get("heartbeat_utc"), (int, float))
        and 0 <= moment - status["heartbeat_utc"] <= 40
    )


def agent_health_text(*, now: float | None = None) -> str:
    """Short, user-facing health report; safe when no agent has ever started."""
    status = read_status()
    moment = time.time() if now is None else now
    heartbeat = status.get("heartbeat_utc")
    detail = str(status.get("message") or "No status reported")
    if agent_alive(now=moment):
        return f"Running — {detail}"
    if isinstance(heartbeat, (int, float)) and heartbeat <= moment:
        age = int(moment - heartbeat)
        return f"Not responding (last report {age}s ago) — {detail}"
    return "Not running — no background heartbeat received yet."


def _single_instance_mutex():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise OSError("Cannot create background agent mutex")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel.CloseHandle(ctypes.c_void_p(handle))
        return None
    return handle


def apply_due_in_foreground_if_agent_missing() -> tuple[bool, str | None]:
    """GUI fallback; a shared Windows mutex prevents racing the real agent.

    Acquiring the mutex *before* reading the due rules is essential: an agent
    may start between a heartbeat check and this attempt. Stamping due rules
    once also prevents a later agent restart from repeating the same update.
    """
    _require_windows()
    if agent_alive():
        return False, None
    handle = _single_instance_mutex()
    if handle is None:
        return False, None  # Agent exists but may be busy or starting.
    try:
        return True, _apply_due_automations()
    finally:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


def diagnostic_lines(*, now: float | None = None) -> list[str]:
    """Read-only troubleshooting for the otherwise invisible background job."""
    moment = time.time() if now is None else now
    config = load_automation()
    lines = [
        f"Background enabled: {config.background_enabled}",
        f"Automation paused: {config.paused}",
        f"Agent: {agent_health_text(now=moment)}",
        f"Saved interval rules: {len(config.rules)}",
    ]
    for rule in config.rules:
        try:
            due_text = (
                f"{datetime.fromtimestamp(rule.next_due_utc).astimezone():%Y-%m-%d %H:%M:%S}"
            )
        except (OSError, OverflowError, ValueError):
            due_text = (
                f"Unix timestamp {rule.next_due_utc} "
                "(local date unavailable)"
            )
        remaining = max(0, int(rule.next_due_utc - moment))
        lines.extend([
            f"- Monitors: {', '.join(rule.monitor_ids)}",
            f"  Enabled: {rule.enabled}; interval: {rule.interval_seconds}s",
            f"  Next due (local): {due_text} "
            f"({'due now' if remaining == 0 else f'in {remaining}s'})",
            f"  Last result: {rule.last_result}",
        ])
    return lines


def _scan_changed_folders(detector, pending_ids: set[str]) -> str | None:
    with WallpaperCatalog() as catalog:
        links = [
            link for collection in catalog.list_collections()
            for link in catalog.list_folder_links(collection.id)
        ]
        linked_ids = {link.id for link in links}
        pending_ids.update(detector.poll(links))
        changed = len(pending_ids)
        added = 0
        for folder_id in tuple(sorted(pending_ids)):
            if folder_id not in linked_ids:
                pending_ids.discard(folder_id)
                continue
            result = scan_folder(catalog, folder_id)
            added += result.added
            pending_ids.discard(folder_id)
    return f"Indexed {changed} changed folder(s), {added} new image(s)." if changed else None


def _run_cycle(detector, pending_ids: set[str]) -> str | None:
    """Keep scheduled wallpapers alive even if the library watcher fails.

    Scanning and wallpaper application are independent jobs. In C3/C3.1 one
    exception from the scanner short-circuited the scheduler for every tick.
    """
    scan_result = None
    scan_failure = None
    try:
        scan_result = _scan_changed_folders(detector, pending_ids)
    except Exception as error:
        scan_failure = f"Library auto-scan failed: {error}"

    try:
        update = _apply_due_automations()
    except Exception as error:
        update = f"Automation check failed: {error}"

    if update and scan_failure:
        return f"{update} | {scan_failure}"
    return update or scan_failure or scan_result


def _apply_due_automations() -> str | None:
    """Execute daily, interval and seasonal triggers in one coordinated plan."""
    config = load_automation()
    # Repair pre-existing overlapping intervals before deciding which are due.
    # The newest saved enabled timer wins; older overlapping timers are paused.
    from wallpaper_orchestrator.automation import normalize_interval_conflicts
    normalized = normalize_interval_conflicts(config)
    if normalized != config:
        save_automation(normalized)
        config = normalized
    if config.paused:
        return "Automations paused by user."
    from PySide6.QtCore import QSettings
    from wallpaper_orchestrator.engine import apply_plan, create_profile_plan
    from wallpaper_orchestrator.platforms.windows import discover_monitors

    moment = datetime.now().astimezone()
    epoch = time.time()
    daily = due_rules(load_schedules(), moment)
    intervals = due_intervals(config, epoch)
    seasons = load_calendar_rules()
    season_error = None
    try:
        previous_seasons = load_seasonal_state()
    except (OSError, ValueError) as error:
        previous_seasons = {}
        season_error = f"Seasonal triggers paused: {error}"
    if not daily and not intervals and not seasons and not previous_seasons:
        return season_error

    profiles = load_profiles()  # Live order and sources, never cached.
    active = {monitor.device_id for monitor in discover_monitors()}
    if not active:
        return "Waiting for an active Windows display session."
    season_decision = SeasonDecision(frozenset(), previous_seasons, ())
    if not season_error:
        season_decision = evaluate_seasons(
            seasons, active, moment, epoch=epoch, previous=previous_seasons,
        )
        if season_decision.new_states != previous_seasons:
            try:
                # Stamp before applying: crashes/restarts must not replay events.
                save_seasonal_state(season_decision.new_states)
            except (OSError, ValueError) as error:
                season_error = f"Seasonal triggers paused: {error}"
                season_decision = SeasonDecision(frozenset(), previous_seasons, ())
    if not daily and not intervals and not season_decision.target_ids:
        return season_error
    daily_targets = resolve_targets(daily, active, profiles)
    interval_targets = resolve_interval_targets(intervals, active, profiles)
    season_targets = {mid for mid in season_decision.target_ids
                      if profiles.get(mid, {}).get("mode", "folder") != "fixed"}
    targets = set(daily_targets.device_ids) | set(interval_targets.device_ids) | season_targets

    daily_ids = set(daily_targets.attempted_ids)
    interval_ids = set(interval_targets.attempted_ids)
    if not daily_ids and not interval_ids and not season_decision.target_ids:
        return "Waiting for a disconnected automation display."

    # Mark attempts *before* applying. A crash, reboot, or repeated DST hour
    # never causes an endless retry loop. Keep unrelated edited rules intact.
    if daily_ids:
        latest = load_schedules()
        save_schedules(stamp_attempts(latest, daily_ids, moment, "Starting..."))
    if interval_ids:
        fresh = load_automation()
        current_ids = {r.id for r in due_intervals(fresh, epoch)}
        interval_ids &= current_ids
        if interval_ids:
            save_automation(advance_intervals(fresh, interval_ids, epoch, "Starting..."))

    if not targets:
        result = "No active shufflable display for the due automation."
    else:
        try:
            settings = QSettings("WallpaperOrchestrator", "WallpaperOrchestrator")
            shared = settings.value("wallpaper_library", "", type=str)
            limits = settings.value("cross_monitor_limits_enabled", False, type=bool)
            assignments, device_ids = create_profile_plan(
                Path(shared) if shared else None, profiles,
                selected_device_ids=targets, respect_theme_limits=limits,
            )
            apply_plan(assignments, device_ids)
            for mid, wallpaper in assignments.items():
                profiles.setdefault(mid, {})["last_image"] = str(wallpaper.path)
            save_profiles(profiles)
            result = f"Applied {len(assignments)} wallpaper(s) at {moment:%H:%M}."
        except Exception as error:
            result = f"Automation failed: {error}"

    if daily_ids:
        save_schedules(stamp_attempts(load_schedules(), daily_ids, moment, result))
    if interval_ids:
        save_automation(record_interval_results(load_automation(), interval_ids, result))
    return f"{result} | {season_error}" if season_error else result


def main() -> int:
    _require_windows()
    handle = _single_instance_mutex()
    if handle is None:
        return 0
    ole = ctypes.windll.ole32
    initialized = ole.CoInitialize(None) in (0, 1)
    detector = FolderChangeDetector()
    pending_ids: set[str] = set()
    last_message = "Background automation started."
    try:
        if not initialized:
            raise RuntimeError("Cannot initialize Windows COM for wallpapers")
        while load_automation().background_enabled:
            try:
                update = _run_cycle(detector, pending_ids)
                if update:
                    last_message = update
            except Exception as error:
                last_message = f"Background agent will retry: {error}"
            _write_status(last_message)
            time.sleep(10)
        _write_status("Background automation stopped.", running=False)
    except Exception as error:
        try:
            _write_status(f"Background agent stopped: {error}", running=False)
        except OSError:
            pass
        return 1
    finally:
        if initialized:
            ole.CoUninitialize()
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--status"]:
        try:
            print("\n".join(diagnostic_lines()))
        except Exception as error:
            print(f"Cannot inspect automation: {error}", file=sys.stderr)
            raise SystemExit(1) from error
    elif len(sys.argv) == 1:
        raise SystemExit(main())
    else:
        print("Usage: python -m wallpaper_orchestrator.background [--status]",
              file=sys.stderr)
        raise SystemExit(2)
