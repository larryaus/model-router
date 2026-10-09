"""Process entry point.

Hooks run on every prompt and every subagent launch, and nearly all of their
start-up cost is imports. So this module imports almost nothing itself, sends
hooks and the status line straight to their handlers, and loads the argparse
command line only for the commands a person types.
"""

import sys
from typing import List


def _hook(argv: List[str]) -> int:
    """Run a hook. Always returns 0: exit code 2 would block the launch."""
    try:
        import os
        import time

        name = argv[0] if argv else ""
        if name == "pre-agent":
            from model_router.hooks.pre_agent import handle
        elif name == "prompt":
            from model_router.hooks.prompt import handle
        else:
            return 0
        from model_router.hooks import run_hook
        from model_router.paths import from_env

        text = run_hook(
            name.replace("-", "_"), handle, sys.stdin.read(),
            from_env(os.environ), int(time.time()), os.environ,
        )
        if text:
            sys.stdout.write(text + "\n")
    except Exception:
        pass
    return 0


def _statusline() -> int:
    try:
        import os
        import time

        from model_router.hooks import statusline
        from model_router.paths import from_env

        sys.stdout.write(
            statusline.run(sys.stdin.read(), from_env(os.environ), int(time.time()))
        )
    except Exception:
        pass
    return 0


def main(argv: List[str]) -> int:
    if argv[:1] == ["hook"]:
        return _hook(argv[1:])
    if argv[:1] == ["statusline"]:
        return _statusline()
    from model_router.cli import main as cli_main

    return cli_main(argv)
