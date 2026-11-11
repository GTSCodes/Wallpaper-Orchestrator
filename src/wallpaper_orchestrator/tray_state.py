"""Human-readable tray status; no dependency on Qt or Windows."""

import time


def next_shuffle_text(config, *, now: float | None = None) -> str:
    """Show the closest interval; daily schedules are tracked separately."""
    if config.paused:
        return "Next interval: paused"
    active = [rule.next_due_utc for rule in config.rules if rule.enabled]
    if not active:
        return "Next interval: none (daily schedules may apply)"
    remaining = max(0, int(min(active) - (time.time() if now is None else now)))
    if remaining == 0:
        return "Next interval: due now"
    if remaining < 60:
        return f"Next interval: in {remaining}s"
    if remaining < 3600:
        return f"Next interval: in {(remaining + 59) // 60}m"
    return f"Next interval: in {(remaining + 3599) // 3600}h"


def automation_state_text(config, *, agent_running: bool) -> str:
    if config.paused:
        return "Automations paused"
    if not config.background_enabled:
        return "Background off (GUI schedules available)"
    return "Background running" if agent_running else "Background not responding"
