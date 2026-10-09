"""Status-line command: capture Claude quota, then relay to the old command.

Claude Code exposes subscription quota only in the status-line input, and
allows one status-line command. This one keeps the quota fields and hands
the untouched input on to whatever command the owner had before.
"""

import json
import subprocess
from typing import Any, Callable

from model_router.config import raw_statusline_passthrough
from model_router.paths import Paths
from model_router.quota.claude import write_snapshot

PASSTHROUGH_TIMEOUT_SECONDS = 2


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
    command = raw_statusline_passthrough(paths.config)
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
