"""C2: pure scheduling rules and durable local-time execution history.

No Qt, Windows COM, filesystem watcher or wallpaper application is imported
here. The GUI calls due_rules() on a short timer and uses the existing engine.
"""

from dataclasses import dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import re
from uuid import uuid4


_CLOCK_PATTERN = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")
ALL_WEEKDAYS = (0, 1, 2, 3, 4, 5, 6)


def schedules_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "WallpaperOrchestrator" / "schedules.json"


@dataclass(frozen=True)
class ScheduleRule:
    id: str
    name: str
    time: str  # Local wall-clock HH:MM, in the computer's current timezone.
    target: str  # "all" or "monitor"
    device_id: str | None
    weekdays: tuple[int, ...] = ALL_WEEKDAYS  # Monday=0, Sunday=6.
    enabled: bool = True
    last_attempt_date: str | None = None  # Local YYYY-MM-DD (also DST guard).
    last_result: str = "Not run yet"

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Schedule ID must be a non-empty string.")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Schedule name must not be empty.")
        if not isinstance(self.time, str) or not _CLOCK_PATTERN.fullmatch(self.time):
            raise ValueError("Schedule time must be HH:MM in 24-hour format.")
        if self.target not in {"all", "monitor"}:
            raise ValueError("Schedule target must be all or monitor.")
        if self.target == "monitor" and (not isinstance(self.device_id, str)
                                         or not self.device_id.strip()):
            raise ValueError("Monitor schedule requires a stable monitor device ID.")
        if self.target == "all" and self.device_id is not None:
            raise ValueError("All-displays schedule cannot hold a monitor ID.")
        if (not isinstance(self.weekdays, tuple) or not self.weekdays
                or any(type(day) is not int or day not in ALL_WEEKDAYS
                       for day in self.weekdays)
                or len(self.weekdays) != len(set(self.weekdays))):
            raise ValueError("Schedule weekdays must be distinct numbers 0–6.")
        if type(self.enabled) is not bool:
            raise ValueError("Schedule enabled must be a boolean.")
        if self.last_attempt_date is not None:
            if not isinstance(self.last_attempt_date, str):
                raise ValueError("Invalid last-attempt date.")
            try:
                parsed = datetime.strptime(self.last_attempt_date, "%Y-%m-%d")
            except ValueError as error:
                raise ValueError("Invalid last-attempt date.") from error
            if parsed.strftime("%Y-%m-%d") != self.last_attempt_date:
                raise ValueError("Invalid last-attempt date.")
        if not isinstance(self.last_result, str):
            raise ValueError("Invalid schedule status.")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "time": self.time,
            "target": self.target, "device_id": self.device_id,
            "weekdays": list(self.weekdays), "enabled": self.enabled,
            "last_attempt_date": self.last_attempt_date,
            "last_result": self.last_result,
        }


def new_rule(
    name: str, time: str, target: str = "all", device_id: str | None = None,
    *, weekdays: tuple[int, ...] = ALL_WEEKDAYS, enabled: bool = True,
    now: datetime | None = None,
) -> ScheduleRule:
    """Create a rule; don't unexpectedly fire a past-time rule on creation."""
    rule = ScheduleRule(uuid4().hex, name, time, target, device_id, weekdays, enabled)
    moment = now or datetime.now().astimezone()
    if moment.hour * 60 + moment.minute >= _minutes(time):
        rule = replace(
            rule, last_attempt_date=moment.date().isoformat(),
            last_result="Starts at the next scheduled occurrence",
        )
    return rule


def _minutes(time: str) -> int:
    hour, minute = map(int, time.split(":"))
    return hour * 60 + minute


def due_rules(rules: list[ScheduleRule], now: datetime) -> list[ScheduleRule]:
    """Return today's due rules once each, catching up after sleep/restart.

    Stamping by *local date* avoids a second run in the repeated hour when
    daylight saving ends. A skipped hour at DST start catches up later today.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Scheduler needs an aware local datetime.")
    today = now.date().isoformat()
    current_minute = now.hour * 60 + now.minute
    return sorted(
        (
            rule for rule in rules
            if rule.enabled and now.weekday() in rule.weekdays
            and rule.last_attempt_date != today
            and _minutes(rule.time) <= current_minute
        ),
        key=lambda rule: (rule.time, rule.id),
    )


def stamp_attempts(
    rules: list[ScheduleRule], attempted_ids: set[str],
    now: datetime, message: str,
) -> list[ScheduleRule]:
    """Persist an attempt before applying wallpaper, including failures."""
    today = now.date().isoformat()
    return [
        replace(rule, last_attempt_date=today, last_result=message)
        if rule.id in attempted_ids else rule
        for rule in rules
    ]


def record_manual_result(
    rules: list[ScheduleRule], attempted_ids: set[str], message: str
) -> list[ScheduleRule]:
    """Report a manual test without blocking the day's automatic run."""
    return [
        replace(rule, last_result=message) if rule.id in attempted_ids else rule
        for rule in rules
    ]


@dataclass(frozen=True)
class ScheduleTargets:
    device_ids: frozenset[str]
    attempted_ids: frozenset[str]
    deferred_ids: frozenset[str]


def resolve_targets(
    due: list[ScheduleRule], active_device_ids: set[str], profiles: dict
) -> ScheduleTargets:
    """Merge simultaneous rules, excluding fixed and disconnected displays.

    Offline monitor rules remain due so they can be attempted when that
    monitor reconnects later the same day. Global rules respect Shuffle All.
    """
    devices: set[str] = set()
    attempted: set[str] = set()
    deferred: set[str] = set()
    for rule in due:
        if rule.target == "all":
            attempted.add(rule.id)
            devices.update(
                device_id for device_id in active_device_ids
                if profiles.get(device_id, {}).get("mode", "folder") != "fixed"
                and profiles.get(device_id, {}).get("shuffle_all", True)
            )
        elif rule.device_id not in active_device_ids:
            deferred.add(rule.id)
        else:
            attempted.add(rule.id)
            if profiles.get(rule.device_id, {}).get("mode", "folder") != "fixed":
                devices.add(rule.device_id)
    return ScheduleTargets(
        frozenset(devices), frozenset(attempted), frozenset(deferred)
    )


def load_schedules(path: Path | None = None) -> list[ScheduleRule]:
    path = path or schedules_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read schedules: {error}") from error
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported schedules format. File was not changed.")
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list):
        raise ValueError("Schedules file must contain a rules list.")
    rules = []
    seen = set()
    for data in raw_rules:
        if not isinstance(data, dict):
            raise ValueError("Invalid schedule entry.")
        try:
            fields = dict(data)
            fields["weekdays"] = tuple(fields.get("weekdays", ALL_WEEKDAYS))
            rule = ScheduleRule(**fields)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid schedule entry: {error}") from error
        if rule.id in seen:
            raise ValueError("Duplicate schedule ID.")
        seen.add(rule.id)
        rules.append(rule)
    return rules


def save_schedules(rules: list[ScheduleRule], path: Path | None = None) -> None:
    """Write atomically and never replace a future-version schedules file."""
    path = path or schedules_path()
    if path.exists():
        load_schedules(path)  # Refuse to overwrite corrupt/future state.
    ids = [rule.id for rule in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate schedule ID.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps({"version": 1, "rules": [r.to_dict() for r in rules]}, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
