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
        if used is None or used < 0 or used > 100:
            continue
        resets = number(raw.get("resets_at"))
        limits[key] = {
            "used_percentage": used,
            "resets_at": int(resets) if resets is not None else None,
        }
    return limits


def write_snapshot(payload: Any, path: Path, now: int) -> bool:
    """Persist the quota windows. Returns False when there were none."""
    limits = extract_rate_limits(payload)
    if not limits:
        return False
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
    windows = [
        Window(limits[key]["used_percentage"], minutes, limits[key]["resets_at"])
        for key, minutes in WINDOW_MINUTES
        if key in limits
    ]
    return PlanQuota(
        "claude", windows, int(captured) if captured is not None else None, True
    )
