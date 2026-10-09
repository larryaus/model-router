"""Status-line command: capture Claude quota, then relay to the old command.

Claude Code exposes subscription quota only in the status-line input, and
allows one status-line command. This one keeps the quota fields and hands
the untouched input on to whatever command the owner had before.
"""

import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

from model_router.config import read_statusline_passthrough
from model_router.fsutil import atomic_write_text
from model_router.paths import Paths
from model_router.quota.claude import write_snapshot

PASSTHROUGH_TIMEOUT_SECONDS = 2


def _recall(cache: Path) -> Optional[str]:
    try:
        with cache.open("r", encoding="utf-8") as handle:
            value = json.load(handle).get("command")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None


def _remember(cache: Path, command: Optional[str]) -> None:
    """Record the last passthrough read from a parseable config."""
    try:
        # The status line renders often; only write when the value changes.
        if cache.exists() and _recall(cache) == command:
            return
        atomic_write_text(cache, json.dumps({"command": command}) + "\n")
    except OSError:
        pass


def run(
    stdin_text: str,
    paths: Paths,
    now: int,
    runner: Callable[..., Any] = subprocess.run,
) -> str:
    try:
        write_snapshot(json.loads(stdin_text), paths.claude_quota, now)
    except Exception:
        # Capture is best effort. The owner's status line matters more.
        pass
    readable, command = read_statusline_passthrough(paths.config)
    cache = paths.state_dir / "statusline-passthrough.json"
    if readable:
        _remember(cache, command)
    else:
        # A typo in the config must not blank the owner's status line or cut
        # off whatever their previous command feeds. Use the last good value.
        command = _recall(cache)
    if not command:
        return ""
    try:
        completed = runner(
            command,
            shell=True,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=PASSTHROUGH_TIMEOUT_SECONDS,
        )
    except Exception:
        return ""
    return completed.stdout if isinstance(completed.stdout, str) else ""
