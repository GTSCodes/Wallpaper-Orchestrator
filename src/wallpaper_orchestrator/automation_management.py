"""C5 automation inventory and conflict advice, independent of the GUI/OS.

Different automation types may deliberately target the same display. The
background agent coalesces simultaneous triggers, so a shared target alone is
not a destructive conflict and never causes an automatic rule deletion.
"""

from dataclasses import dataclass
from datetime import datetime
from itertools import combinations

from wallpaper_orchestrator.automation import IntervalRule
from wallpaper_orchestrator.calendar_rules import CalendarRule, matches_calendar
from wallpaper_orchestrator.scheduler import ScheduleRule


@dataclass(frozen=True)
class AutomationEntry:
    kind: str
    id: str
    name: str
    enabled: bool
    target_ids: frozenset[str]
    global_target: bool = False


@dataclass(frozen=True)
class AutomationOverlap:
    first: AutomationEntry
    second: AutomationEntry
    shared_monitors: frozenset[str]
    severity: str  # "warning" needs user review; "info" is coordinated.
    explanation: str


def inventory(
    intervals: list[IntervalRule], schedules: list[ScheduleRule],
    seasons: list[CalendarRule],
) -> list[AutomationEntry]:
    entries = [AutomationEntry("Interval", rule.id, f"Every {rule.interval_seconds}s",
                               rule.enabled, frozenset(rule.monitor_ids)) for rule in intervals]
    entries += [AutomationEntry("Daily", rule.id, rule.name, rule.enabled,
                                frozenset((rule.device_id,)) if rule.target == "monitor" else frozenset(),
                                rule.target == "all") for rule in schedules]
    entries += [AutomationEntry("Seasonal", rule.id, rule.name, rule.enabled,
                                frozenset(rule.target_ids), not bool(rule.target_ids)) for rule in seasons]
    return entries


def find_overlaps(
    entries: list[AutomationEntry], active_monitors: set[str],
    *, now: datetime | None = None, seasons: list[CalendarRule] | None = None,
) -> list[AutomationOverlap]:
    """Explain potential collisions; no rule is silently disabled or deleted."""
    date = now or datetime.now().astimezone()
    seasons_by_id = {s.id: s for s in seasons or []}
    results = []
    for first, second in combinations(entries, 2):
        if not first.enabled or not second.enabled:
            continue
        left = set(active_monitors) if first.global_target else set(first.target_ids)
        right = set(active_monitors) if second.global_target else set(second.target_ids)
        shared = left & right
        if not shared:
            continue
        if first.kind == second.kind == "Seasonal":
            earlier = seasons_by_id.get(first.id)
            later = seasons_by_id.get(second.id)
            if earlier and later and not any(
                matches_calendar(earlier, date, mid) and matches_calendar(later, date, mid)
                for mid in shared
            ):
                continue
            severity, explanation = ("warning", "Active seasonal filters overlap; highest priority wins")
        elif first.kind == second.kind == "Interval":
            severity, explanation = ("warning", "Separate interval timers target the same display")
        else:
            severity, explanation = ("info", "Shared display; coincident updates are combined by the agent")
        results.append(AutomationOverlap(first, second, frozenset(shared), severity, explanation))
    return results
