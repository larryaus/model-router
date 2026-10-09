"""Run one task through `codex exec` inside a fixed sandbox.

The worker agents call this instead of building a codex command themselves,
so the sandbox, working directory, and flags are decided by code that is
tested, not by a model following instructions.
"""

import os
import subprocess
import tempfile
from typing import Any, Callable, List, Tuple

from model_router.config import CODEX_EFFORTS

SANDBOX = {"read": "read-only", "write": "workspace-write"}
EFFORT_PREFIX = "ROUTER_EFFORT:"
DEFAULT_EFFORT = "medium"
ERROR_TAIL_CHARS = 2000


def split_effort(text: str) -> Tuple[str, str]:
    """Take the effort line off the front of a task.

    The effort is untrusted text, so anything outside the known list is
    replaced with the default before it can reach a command line.
    """
    first, _newline, rest = text.partition("\n")
    if not first.startswith(EFFORT_PREFIX):
        return DEFAULT_EFFORT, text
    effort = first[len(EFFORT_PREFIX):].strip()
    return (effort if effort in CODEX_EFFORTS else DEFAULT_EFFORT), rest


def build_command(mode: str, effort: str, cwd: str, out_file: str) -> List[str]:
    return [
        "codex", "exec",
        "--sandbox", SANDBOX[mode],
        "--cd", cwd,
        "--skip-git-repo-check",
        "-c", 'model_reasoning_effort="%s"' % effort,
        "--output-last-message", out_file,
        "-",
    ]


def run(
    mode: str,
    prompt_file: str,
    cwd: str,
    runner: Callable[..., Any] = subprocess.run,
) -> Tuple[int, str]:
    if mode not in SANDBOX:
        return 2, "codex-run: mode must be read or write"
    try:
        with open(prompt_file, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        return 2, "codex-run: could not read the prompt file: %s" % exc
    effort, task = split_effort(text)
    if not task.strip():
        return 2, "codex-run: the task is empty"

    descriptor, out_file = tempfile.mkstemp(prefix="model-router-codex-", suffix=".txt")
    os.close(descriptor)
    try:
        try:
            completed = runner(
                build_command(mode, effort, cwd, out_file),
                input=task,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return 127, "Codex could not be started: %s" % exc
        try:
            with open(out_file, "r", encoding="utf-8") as handle:
                message = handle.read().strip()
        except OSError:
            message = ""
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return completed.returncode, "Codex failed (exit %d).\n%s" % (
                completed.returncode, detail[-ERROR_TAIL_CHARS:],
            )
        if not message:
            return 1, "Codex finished but returned no answer."
        return 0, message
    finally:
        try:
            os.unlink(out_file)
        except OSError:
            pass
