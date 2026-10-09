"""ChatGPT plan quota, read from the Codex CLI's session logs.

Codex records its rate limits in the session log each time it runs. This
reader never touches Codex credentials; it only reads those log lines.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from model_router.quota.types import PlanQuota, Window, number, unknown, with_expiry

DEFAULT_MAX_FILES = 20
DEFAULT_TAIL_BYTES = 256 * 1024


def _recent_files(sessions_dir: Path, max_files: int) -> List[Tuple[float, Path]]:
    found: List[Tuple[float, Path]] = []
    try:
        for path in Path(sessions_dir).glob("**/rollout-*.jsonl"):
            try:
                found.append((path.stat().st_mtime, path))
            except OSError:
                continue
    except OSError:
        return []
    found.sort(key=lambda item: item[0], reverse=True)
    return found[:max_files]


def _tail_lines(path: Path, tail_bytes: int) -> List[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - tail_bytes))
            data = handle.read()
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()


def _windows(rate_limits: Dict[str, Any]) -> List[Window]:
    windows: List[Window] = []
    for key in ("primary", "secondary"):
        raw = rate_limits.get(key)
        if not isinstance(raw, dict):
            continue
        used = number(raw.get("used_percent"))
        minutes = number(raw.get("window_minutes"))
        if used is None or used < 0 or minutes is None or minutes <= 0:
            continue
        resets = number(raw.get("resets_at"))
        windows.append(
            Window(
                min(used, 100.0),
                int(minutes),
                int(resets) if resets is not None else None,
            )
        )
    return windows


def _captured_at(record: Dict[str, Any], fallback: float) -> int:
    raw = record.get("timestamp")
    if isinstance(raw, str):
        from datetime import datetime  # Only needed once a reading is found.

        try:
            return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    return int(fallback)


def read_codex_quota(
    sessions_dir: Path,
    max_files: int = DEFAULT_MAX_FILES,
    tail_bytes: int = DEFAULT_TAIL_BYTES,
) -> PlanQuota:
    for mtime, path in _recent_files(sessions_dir, max_files):
        for line in reversed(_tail_lines(path, tail_bytes)):
            if '"rate_limits"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            payload = record.get("payload")
            holder = payload if isinstance(payload, dict) else record
            rate_limits = holder.get("rate_limits")
            if not isinstance(rate_limits, dict):
                continue
            windows = _windows(rate_limits)
            if windows:
                captured_at = _captured_at(record, mtime)
                windows = [with_expiry(window, captured_at) for window in windows]
                return PlanQuota("codex", windows, captured_at, True)
    return unknown("codex")
