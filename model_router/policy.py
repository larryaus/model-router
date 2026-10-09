"""The routing policy. Pure: no I/O, and the current time is passed in."""

from typing import Optional

from model_router.config import Config
from model_router.quota.types import (
    WEEK_MINUTES,
    PlanQuota,
    Window,
    effective_used_pct,
)

LEVEL_ORDER = ("normal", "conserve", "critical", "exhausted")
ALLOWED_WEIGHTS = {
    "normal": ("heavy", "medium", "light"),
    "conserve": ("medium", "light"),
    "critical": ("light",),
    "exhausted": (),
}


def projected_pct(window: Window, now: int, warmup: float) -> Optional[float]:
    """Usage at the end of the window if the current pace holds.

    Undefined when the window has no usable reset time, has not started, or
    is still inside the warmup, where early readings are too noisy.
    """
    if window.resets_at is None or window.resets_at <= now:
        return None
    if window.window_minutes <= 0:
        return None
    seconds = window.window_minutes * 60.0
    elapsed = max(0.0, min(1.0, 1.0 - (window.resets_at - now) / seconds))
    if elapsed <= 0.0 or elapsed < warmup:
        return None
    return window.used_pct / elapsed


def window_level(window: Window, now: int, config: Config) -> str:
    used = effective_used_pct(window, now)
    if used >= config.exhausted:
        return "exhausted"
    if used >= config.critical:
        return "critical"
    if used >= config.conserve:
        return "conserve"
    projected = projected_pct(window, now, config.warmup)
    if projected is not None and projected >= 100.0:
        return "conserve"
    return "normal"


def plan_level(quota: PlanQuota, now: int, config: Config) -> str:
    level = "normal"
    for window in quota.windows:
        candidate = window_level(window, now, config)
        if LEVEL_ORDER.index(candidate) > LEVEL_ORDER.index(level):
            level = candidate
    return level


def weekly_projection(quota: PlanQuota, now: int, config: Config) -> Optional[float]:
    for window in quota.windows:
        if window.window_minutes == WEEK_MINUTES:
            return projected_pct(window, now, config.warmup)
    return None
