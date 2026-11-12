"""C3: interval automation and background-agent configuration (no Qt)."""

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import time
from uuid import uuid4


def automation_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "WallpaperOrchestrator" / "automation.json"


@dataclass(frozen=True)
class IntervalRule:
    id: str
    monitor_ids: tuple[str, ...]
    interval_seconds: int
    next_due_utc: float
    enabled: bool = True
    last_result: str = "Not run yet"

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Invalid automation ID")
        if (not isinstance(self.monitor_ids, tuple) or not self.monitor_ids
                or any(not isinstance(mid, str) or not mid for mid in self.monitor_ids)
                or len(self.monitor_ids) != len(set(self.monitor_ids))):
            raise ValueError("Automation must have distinct monitor IDs")
        if type(self.interval_seconds) is not int or not 60 <= self.interval_seconds <= 604800:
            raise ValueError("Interval must be between 1 minute and 7 days")
        if (type(self.next_due_utc) not in {int, float}
                or not 0 <= self.next_due_utc < 1e13):
            raise ValueError("Invalid next execution time")
        if type(self.enabled) is not bool or not isinstance(self.last_result, str):
            raise ValueError("Invalid automation status")

    def to_dict(self):
        return {
            "id": self.id, "monitor_ids": list(self.monitor_ids),
            "interval_seconds": self.interval_seconds,
            "next_due_utc": self.next_due_utc,
            "enabled": self.enabled, "last_result": self.last_result,
        }


@dataclass(frozen=True)
class AutomationConfig:
    background_enabled: bool = False
    rules: tuple[IntervalRule, ...] = ()
    paused: bool = False


@dataclass(frozen=True)
class IntervalTargets:
    device_ids: frozenset[str]
    attempted_ids: frozenset[str]
    deferred_ids: frozenset[str]


def resolve_interval_targets(
    rules: list[IntervalRule], active: set[str], profiles: dict
) -> IntervalTargets:
    """Apply a due interval to connected members, even when others are offline.

    A disconnected member remains saved in its rule and will rejoin at the
    next interval when Windows reports it as active. Never shuffle fixed ones.
    """
    devices: set[str] = set()
    attempted: set[str] = set()
    deferred: set[str] = set()
    for rule in rules:
        online = set(rule.monitor_ids) & active
        if not online:
            deferred.add(rule.id)
            continue
        attempted.add(rule.id)
        devices.update(
            mid for mid in online
            if profiles.get(mid, {}).get("mode", "folder") != "fixed"
        )
    return IntervalTargets(
        frozenset(devices), frozenset(attempted), frozenset(deferred)
    )


def load_automation(path: Path | None = None) -> AutomationConfig:
    path = path or automation_path()
    if not path.exists():
        return AutomationConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read automation: {error}") from error
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("Unsupported automation format; file preserved")
    if type(raw.get("background_enabled")) is not bool or not isinstance(raw.get("rules"), list):
        raise ValueError("Invalid automation format")
    rules = []
    seen = set()
    for item in raw["rules"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid interval rule")
        data = dict(item)
        try:
            data["monitor_ids"] = tuple(data["monitor_ids"])
            rule = IntervalRule(**data)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid interval rule: {error}") from error
        if rule.id in seen:
            raise ValueError("Duplicate interval rule ID")
        seen.add(rule.id)
        rules.append(rule)
    paused = raw.get("paused", False)
    if type(paused) is not bool:
        raise ValueError("Invalid automation pause setting")
    return AutomationConfig(raw["background_enabled"], tuple(rules), paused)


def save_automation(config: AutomationConfig, path: Path | None = None) -> None:
    path = path or automation_path()
    if path.exists():
        load_automation(path)  # Never replace future/corrupt state.
    if type(config.background_enabled) is not bool or type(config.paused) is not bool:
        raise ValueError("Invalid background or pause setting")
    ids = [rule.id for rule in config.rules]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate interval rule IDs")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps({
            "version": 1,
            "background_enabled": config.background_enabled,
            "paused": config.paused,
            "rules": [rule.to_dict() for rule in config.rules],
        }, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def matching_rule(config: AutomationConfig, monitor_ids) -> IntervalRule | None:
    selected = frozenset(monitor_ids)
    return next((r for r in config.rules if frozenset(r.monitor_ids) == selected), None)


def set_interval(
    config: AutomationConfig, monitor_ids, interval_seconds: int,
    *, now: float | None = None,
) -> AutomationConfig:
    """One interval per exact monitor group; uses profiles' live order."""
    ids = tuple(sorted(set(monitor_ids)))
    existing = matching_rule(config, ids)
    moment = time.time() if now is None else now
    rule = IntervalRule(
        existing.id if existing else uuid4().hex, ids,
        interval_seconds, moment + interval_seconds, True,
        "Starts after the next interval",
    )
    updated = [r for r in config.rules if not existing or r.id != existing.id]
    updated.append(rule)
    return enforce_interval_exclusivity(replace(config, rules=tuple(updated)), rule.id)



def overlapping_intervals(config: AutomationConfig, monitor_ids,
                          *, exclude_id: str | None = None) -> tuple[IntervalRule, ...]:
    """Return enabled intervals that share one or more stable monitor IDs."""
    selected = set(monitor_ids)
    return tuple(rule for rule in config.rules
                 if rule.enabled and rule.id != exclude_id
                 and selected.intersection(rule.monitor_ids))


def enforce_interval_exclusivity(config: AutomationConfig,
                                 winning_id: str) -> AutomationConfig:
    """One enabled interval owner per display; pause, never delete, losers.

    Apply this operation *before* the single atomic save_automation call.
    Daily/seasonal rules are deliberately separate: a season is a filter,
    while a daily schedule is an independent trigger managed in Schedules.
    """
    winner = next((r for r in config.rules if r.id == winning_id), None)
    if winner is None:
        raise ValueError("Unknown interval rule")
    if not winner.enabled:
        return config
    losers = {r.id for r in overlapping_intervals(
        config, winner.monitor_ids, exclude_id=winner.id)}
    return replace(config, rules=tuple(
        replace(r, enabled=False, last_result=(
            "Paused because another interval uses a shared display"
        )) if r.id in losers else r for r in config.rules
    ))


def normalize_interval_conflicts(config: AutomationConfig) -> AutomationConfig:
    """Upgrade pre-C7 overlapping timers deterministically (last enabled wins)."""
    result = config
    for rule in reversed(config.rules):
        if rule.enabled and any(r.id == rule.id and r.enabled for r in result.rules):
            result = enforce_interval_exclusivity(result, rule.id)
    return result


def remove_interval(config: AutomationConfig, monitor_ids) -> AutomationConfig:
    selected = frozenset(monitor_ids)
    return replace(config, rules=tuple(
        r for r in config.rules if frozenset(r.monitor_ids) != selected
    ))


def due_intervals(config: AutomationConfig, now: float) -> list[IntervalRule]:
    return [rule for rule in config.rules
            if rule.enabled and rule.next_due_utc <= now]


def advance_intervals(
    config: AutomationConfig, ids: set[str], now: float, message: str
) -> AutomationConfig:
    """Coalesce missed intervals into one attempt and start a fresh period."""
    return replace(config, rules=tuple(
        replace(rule, next_due_utc=now + rule.interval_seconds,
                last_result=message) if rule.id in ids else rule
        for rule in config.rules
    ))


def record_interval_results(
    config: AutomationConfig, ids: set[str], message: str
) -> AutomationConfig:
    return replace(config, rules=tuple(
        replace(rule, last_result=message) if rule.id in ids else rule
        for rule in config.rules
    ))
