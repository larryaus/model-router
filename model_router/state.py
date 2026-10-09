"""Small per-session state: what each session has already been told."""

import json
from pathlib import Path
from typing import Any, Dict

from model_router.fsutil import atomic_write_text

SESSION_TTL_SECONDS = 7 * 24 * 3600


def load_state(path: Path) -> Dict[str, Any]:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return {"sessions": {}}
    if not isinstance(state, dict) or not isinstance(state.get("sessions"), dict):
        return {"sessions": {}}
    return state


def session_entry(state: Dict[str, Any], session_id: str, now: int) -> Dict[str, Any]:
    entry = state["sessions"].get(session_id)
    if not isinstance(entry, dict):
        entry = {}
        state["sessions"][session_id] = entry
    entry["seen_at"] = int(now)
    return entry


def save_state(path: Path, state: Dict[str, Any], now: int) -> None:
    cutoff = now - SESSION_TTL_SECONDS
    state["sessions"] = {
        session_id: entry
        for session_id, entry in state["sessions"].items()
        if isinstance(entry, dict) and entry.get("seen_at", 0) >= cutoff
    }
    atomic_write_text(
        path, json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
    )
