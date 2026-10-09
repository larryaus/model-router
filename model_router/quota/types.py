"""The one shape every quota reader produces."""

import math
import time
from typing import Any, List, NamedTuple, Optional

FIVE_HOUR_MINUTES = 300
WEEK_MINUTES = 10080


class Window(NamedTuple):
    used_pct: float
    window_minutes: int
    resets_at: Optional[int]


class PlanQuota(NamedTuple):
    plan: str
    windows: List[Window]
    captured_at: Optional[int]
    known: bool


def unknown(plan: str) -> PlanQuota:
    return PlanQuota(plan, [], None, False)


def number(value: Any) -> Optional[float]:
    """Return value as a finite float, or None for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def effective_used_pct(window: Window, now: int) -> float:
    """A window whose reset time has passed has started again at zero."""
    if window.resets_at is not None and window.resets_at <= now:
        return 0.0
    return window.used_pct


def with_expiry(window: Window, captured_at: Optional[int]) -> Window:
    """Give a window with no reset time the latest reset it could have.

    A window cannot outlive its own length, so a reading taken at
    `captured_at` has certainly reset one window-length later. Without this,
    a reading with no reset time would count against the plan forever.
    """
    if window.resets_at is not None or captured_at is None:
        return window
    return window._replace(resets_at=captured_at + window.window_minutes * 60)


def window_label(window: Window) -> str:
    if window.window_minutes == FIVE_HOUR_MINUTES:
        return "5h"
    if window.window_minutes == WEEK_MINUTES:
        return "weekly"
    return "%dm" % window.window_minutes


def format_time(timestamp: int) -> str:
    return time.strftime("%a %H:%M", time.localtime(timestamp))
