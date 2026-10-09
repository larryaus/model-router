"""Hook adapters, and the wrapper that makes every one of them fail open."""

import json
from typing import Any, Callable, Mapping

from model_router.decision_log import append_record
from model_router.paths import Paths


def run_hook(
    name: str,
    handle: Callable[..., Any],
    stdin_text: str,
    paths: Paths,
    now: int,
    env: Mapping[str, str],
) -> str:
    """Run a hook handler and return the text to print.

    Any failure returns "", which Claude Code treats as "no opinion", so the
    launch or prompt proceeds exactly as if the router were not installed.
    """
    try:
        response = handle(json.loads(stdin_text), paths, now, env)
        return json.dumps(response) if response is not None else ""
    except Exception as exc:
        try:
            append_record(paths.log, {
                "ts": int(now),
                "event": "hook_error",
                "hook": name,
                "error": "%s: %s" % (type(exc).__name__, exc),
            })
        except Exception:
            pass
        return ""
