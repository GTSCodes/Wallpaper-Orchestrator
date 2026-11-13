"""C5 seasonal triggers: durable, per-monitor transitions and periodic runs.

The C4 calendar remains the source of truth for which rule wins. This module
observes changes in the effective rule, not the number of matching rules, so
overlapping seasons do not shuffle a display twice at a boundary.
"""

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from wallpaper_orchestrator.automation import automation_path
from wallpaper_orchestrator.calendar_rules import CalendarRule, active_calendar_rule


@dataclass(frozen=True)
class SeasonState:
    rule_id: str | None = None
    apply_on_end: bool = False
    last_shuffle_utc: float = 0.0

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "apply_on_end": self.apply_on_end,
                "last_shuffle_utc": self.last_shuffle_utc}


@dataclass(frozen=True)
class SeasonDecision:
    target_ids: frozenset[str]
    new_states: dict[str, SeasonState]
    reasons: tuple[str, ...]


def seasonal_state_path() -> Path:
    return automation_path().with_name("seasonal-state.json")


def load_seasonal_state(path: Path | None = None) -> dict[str, SeasonState]:
    target = path or seasonal_state_path()
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read seasonal state: {error}") from error
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("monitors"), dict):
        raise ValueError("Unsupported seasonal state; file preserved")
    states = {}
    for key, value in raw["monitors"].items():
        if not isinstance(key, str) or not key or not isinstance(value, dict):
            raise ValueError("Invalid seasonal state entry")
        try:
            state = SeasonState(**value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid seasonal state: {error}") from error
        if (state.rule_id is not None and (not isinstance(state.rule_id, str) or not state.rule_id)
                or type(state.apply_on_end) is not bool
                or type(state.last_shuffle_utc) not in {int, float}
                or not 0 <= state.last_shuffle_utc < 1e13):
            raise ValueError("Invalid seasonal state values")
        states[key] = state
    return states


def save_seasonal_state(states: Mapping[str, SeasonState], path: Path | None = None) -> None:
    target = path or seasonal_state_path()
    if target.exists():
        load_seasonal_state(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps({
            "version": 1, "monitors": {key: value.to_dict() for key, value in states.items()},
        }, indent=2), encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def evaluate_seasons(
    rules: list[CalendarRule], monitor_ids: set[str], now: datetime,
    *, epoch: float, previous: Mapping[str, SeasonState],
) -> SeasonDecision:
    """Compute a single due set and the state to persist before applying.

    Disconnected monitors are *not* evaluated; their prior observations remain
    until reconnecting, when missed transitions coalesce into one update.
    """
    states = dict(previous)
    targets: set[str] = set()
    reasons: list[str] = []
    for mid in sorted(monitor_ids):
        current = active_calendar_rule(rules, now, mid)
        former = states.get(mid, SeasonState())
        current_id = current.id if current else None
        if former.rule_id != current_id:
            if current and current.apply_on_start or former.rule_id and former.apply_on_end:
                targets.add(mid)
                reasons.append(f"Season changed on {mid}")
            states[mid] = SeasonState(current_id, current.apply_on_end if current else False, epoch)
        elif current is not None:
            interval = current.shuffle_interval_seconds
            if interval and epoch >= former.last_shuffle_utc + interval:
                targets.add(mid)
                reasons.append(f"Season interval due on {mid}")
                states[mid] = SeasonState(current_id, current.apply_on_end, epoch)
            else:
                # Edited rule behaviour takes effect even without a transition.
                states[mid] = SeasonState(current_id, current.apply_on_end, former.last_shuffle_utc)
    return SeasonDecision(frozenset(targets), states, tuple(reasons))
