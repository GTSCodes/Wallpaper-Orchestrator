"""C4: durable, local-time seasonal selection policies (no Qt or Windows).

Calendar rules do not trigger wallpaper changes. Existing manual/interval/daily
triggers consult the active policy when selecting images for each monitor.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
from uuid import uuid4


ALL_WEEKDAYS = (0, 1, 2, 3, 4, 5, 6)
TIME_PATTERN = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")
DAY_PATTERN = re.compile(r"[0-1][0-9]-[0-3][0-9]\Z")


def calendar_rules_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "WallpaperOrchestrator" / "calendar_rules.json"


def _check_ids(values: tuple[str, ...], field: str) -> None:
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value.strip() for value in values
    ) or len(values) != len(set(values)):
        raise ValueError(f"{field} must contain distinct, nonempty IDs.")


def _valid_day(value: str | None) -> bool:
    if not isinstance(value, str) or not DAY_PATTERN.fullmatch(value):
        return False
    try:
        month, day = map(int, value.split("-"))
        date(2000, month, day)  # Leap-year base accepts February 29.
        return True
    except ValueError:
        return False


def _minute(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


@dataclass(frozen=True)
class CalendarRule:
    id: str
    name: str
    priority: int = 50
    enabled: bool = True
    # No target IDs means all eligible monitors; otherwise a stable-ID group.
    target_ids: tuple[str, ...] = ()
    period: str = "months"  # months or range (inclusive MM-DD, may wrap year).
    months: tuple[int, ...] = (12,)
    start_date: str | None = None
    end_date: str | None = None
    weekdays: tuple[int, ...] = ALL_WEEKDAYS
    all_day: bool = True
    start_time: str = "09:00"
    end_time: str = "21:00"
    # IDs survive renames; collection filters restrict current subscriptions.
    collection_ids: tuple[str, ...] = ()
    include_tag_ids: tuple[str, ...] = ()
    exclude_tag_ids: tuple[str, ...] = ()
    behavior: str = "prefer"  # prefer => fall back; require => explain failure.

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Calendar rule needs an ID.")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Calendar rule needs a name.")
        if type(self.priority) is not int or not 0 <= self.priority <= 100:
            raise ValueError("Calendar priority must be between 0 and 100.")
        if type(self.enabled) is not bool or type(self.all_day) is not bool:
            raise ValueError("Calendar enabled/all-day flags must be boolean.")
        for field in ("target_ids", "collection_ids", "include_tag_ids", "exclude_tag_ids"):
            _check_ids(getattr(self, field), field)
        if set(self.include_tag_ids) & set(self.exclude_tag_ids):
            raise ValueError("A calendar rule cannot include and exclude the same tag.")
        if not (self.collection_ids or self.include_tag_ids or self.exclude_tag_ids):
            raise ValueError("Select at least one collection or tag filter.")
        if self.period not in {"months", "range"}:
            raise ValueError("Period must be months or range.")
        if self.period == "months":
            if (not isinstance(self.months, tuple) or not self.months
                    or any(type(m) is not int or not 1 <= m <= 12 for m in self.months)
                    or len(self.months) != len(set(self.months))
                    or self.start_date is not None or self.end_date is not None):
                raise ValueError("Select distinct months 1–12 without date-range values.")
        elif (self.months or not _valid_day(self.start_date)
              or not _valid_day(self.end_date)):
            raise ValueError("A date-range rule needs valid MM-DD start and end dates.")
        if (not isinstance(self.weekdays, tuple) or not self.weekdays
                or any(type(day) is not int or day not in ALL_WEEKDAYS
                       for day in self.weekdays)
                or len(self.weekdays) != len(set(self.weekdays))):
            raise ValueError("Select one or more distinct weekdays 0–6.")
        if (not isinstance(self.start_time, str) or not TIME_PATTERN.fullmatch(self.start_time)
                or not isinstance(self.end_time, str)
                or not TIME_PATTERN.fullmatch(self.end_time)):
            raise ValueError("Calendar times must be HH:MM.")
        if not self.all_day and self.start_time == self.end_time:
            raise ValueError("Time window must have different start and end times.")
        if self.behavior not in {"prefer", "require"}:
            raise ValueError("Behavior must be prefer or require.")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "priority": self.priority,
            "enabled": self.enabled, "target_ids": list(self.target_ids),
            "period": self.period, "months": list(self.months),
            "start_date": self.start_date, "end_date": self.end_date,
            "weekdays": list(self.weekdays), "all_day": self.all_day,
            "start_time": self.start_time, "end_time": self.end_time,
            "collection_ids": list(self.collection_ids),
            "include_tag_ids": list(self.include_tag_ids),
            "exclude_tag_ids": list(self.exclude_tag_ids),
            "behavior": self.behavior,
        }


def create_calendar_rule(name: str, **kwargs) -> CalendarRule:
    return CalendarRule(uuid4().hex, name, **kwargs)


def matches_calendar(rule: CalendarRule, now: datetime, device_id: str) -> bool:
    """Time windows crossing midnight belong to the day they started.

    `now` must be timezone-aware so the OS local calendar handles DST. Rules
    operate on wall-clock time; they are never applied twice by this module.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Calendar rules require an aware local datetime.")
    if not rule.enabled or (rule.target_ids and device_id not in rule.target_ids):
        return False
    anchor_day = now.date()
    if not rule.all_day:
        minute = now.hour * 60 + now.minute
        start, end = _minute(rule.start_time), _minute(rule.end_time)
        if start < end:
            if not start <= minute < end:
                return False
        elif minute >= start:
            pass  # Evening of the selected day.
        elif minute < end:
            anchor_day -= timedelta(days=1)  # Morning after an overnight rule.
        else:
            return False
    if anchor_day.weekday() not in rule.weekdays:
        return False
    if rule.period == "months":
        return anchor_day.month in rule.months
    value = anchor_day.strftime("%m-%d")
    assert rule.start_date is not None and rule.end_date is not None
    if rule.start_date <= rule.end_date:
        return rule.start_date <= value <= rule.end_date
    return value >= rule.start_date or value <= rule.end_date


def active_calendar_rule(
    rules: list[CalendarRule], now: datetime, device_id: str,
) -> CalendarRule | None:
    """Highest priority wins; at equal priority target-specific beats global."""
    eligible = [r for r in rules if matches_calendar(r, now, device_id)]
    eligible.sort(key=lambda r: (-r.priority, not bool(r.target_ids), r.name.casefold(), r.id))
    return eligible[0] if eligible else None


def load_calendar_rules(path: Path | None = None) -> list[CalendarRule]:
    path = path or calendar_rules_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read calendar rules: {error}") from error
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("rules"), list):
        raise ValueError("Unsupported calendar rules format; file preserved.")
    seen = set()
    result = []
    for value in raw["rules"]:
        if not isinstance(value, dict):
            raise ValueError("Invalid calendar entry.")
        data = dict(value)
        for key in ("target_ids", "months", "weekdays", "collection_ids",
                    "include_tag_ids", "exclude_tag_ids"):
            if key not in data or not isinstance(data[key], list):
                raise ValueError(f"Calendar entry missing {key} list.")
            data[key] = tuple(data[key])
        try:
            rule = CalendarRule(**data)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid calendar entry: {error}") from error
        if rule.id in seen:
            raise ValueError("Duplicate calendar rule ID.")
        seen.add(rule.id)
        result.append(rule)
    return result


def save_calendar_rules(rules: list[CalendarRule], path: Path | None = None) -> None:
    """Atomic JSON save; never overwrite corrupt or newer schema files."""
    path = path or calendar_rules_path()
    if path.exists():
        load_calendar_rules(path)
    ids = [rule.id for rule in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate calendar rule IDs.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps({
            "version": 1, "rules": [r.to_dict() for r in rules],
        }, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
