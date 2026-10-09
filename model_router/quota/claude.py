"""Claude plan quota, captured from Claude Code's status-line input.

Only the two quota windows are ever stored. The surrounding session
metadata in the status-line payload is dropped.
"""

import json
from pathlib import Path
from typing import Any, Dict

from model_router.fsutil import atomic_write_text
from model_router.quota.types import (
    FIVE_HOUR_MINUTES,
    WEEK_MINUTES,
    PlanQuota,
    Window,
    number,
    unknown,
    with_expiry,
)

WINDOW_MINUTES = (("five_hour", FIVE_HOUR_MINUTES), ("seven_day", WEEK_MINUTES))


def extract_rate_limits(payload: Any) -> Dict[str, Dict[str, Any]]:
    """Return the sanitized quota windows, or {} when none are usable."""
    if not isinstance(payload, dict):
        return {}
    raw_limits = payload.get("rate_limits")
    if not isinstance(raw_limits, dict):
        return {}
    limits: Dict[str, Dict[str, Any]] = {}
    for key, _minutes in WINDOW_MINUTES:
        raw = raw_limits.get(key)
        if not isinstance(raw, dict):
            continue
        used = number(raw.get("used_percentage"))
        if used is None or used < 0:
            continue
        resets = number(raw.get("resets_at"))
        limits[key] = {
            # Over 100 still means "full": dropping it would read as normal.
            "used_percentage": min(used, 100.0),
            "resets_at": int(resets) if resets is not None else None,
        }
    return limits


def _still_current(path: Path, now: int) -> Dict[str, Dict[str, Any]]:
    """Windows from the existing snapshot whose reset is known and ahead."""
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            previous = json.load(handle)
    except (OSError, ValueError):
        return {}
    return {
        key: window
        for key, window in extract_rate_limits(previous).items()
        if window["resets_at"] is not None and window["resets_at"] > now
    }


def write_snapshot(payload: Any, path: Path, now: int) -> bool:
    """Persist the quota windows. Returns False when there were none.

    A payload can carry one window without the other. The missing window's
    last reading stays valid until its reset, so it is kept, not erased.
    """
    limits = extract_rate_limits(payload)
    if not limits:
        return False
    merged = _still_current(path, now)
    merged.update(limits)
    limits = merged
    snapshot = {"captured_at": int(now), "rate_limits": limits}
    atomic_write_text(
        path, json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return True


def read_claude_quota(path: Path) -> PlanQuota:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, ValueError):
        return unknown("claude")
    limits = extract_rate_limits(snapshot)
    if not limits:
        return unknown("claude")
    captured = number(snapshot.get("captured_at"))
    captured_at = int(captured) if captured is not None else None
    windows = [
        with_expiry(
            Window(limits[key]["used_percentage"], minutes, limits[key]["resets_at"]),
            captured_at,
        )
        for key, minutes in WINDOW_MINUTES
        if key in limits
    ]
    return PlanQuota("claude", windows, captured_at, True)
