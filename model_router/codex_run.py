"""Run one task through `codex exec` inside a fixed sandbox.

The worker agents call this instead of building a codex command themselves,
so the sandbox, working directory, and flags are decided by code that is
tested, not by a model following instructions. It starts Codex only for a
launch the pre-launch hook redirected: see `grants`.
"""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from model_router import grants

SANDBOX = {"read": "read-only", "write": "workspace-write"}
GRANT_PREFIX = "ROUTER_GRANT:"
ERROR_TAIL_CHARS = 2000
NO_GRANT = (
    "codex-run: no valid router grant. This command runs only for a launch "
    "the model router redirected, once, shortly after the redirect."
)


def split_grant(text: str) -> Tuple[Optional[str], str]:
    """Take the grant line off the front of a task: (nonce, remaining task)."""
    first, _newline, rest = text.partition("\n")
    if not first.startswith(GRANT_PREFIX):
        return None, text
    return first[len(GRANT_PREFIX):].strip(), rest


def build_command(
    mode: str, effort: str, cwd: str, out_file: str, model: Optional[str] = None
) -> List[str]:
    command = [
        "codex", "exec",
        # The owner's Codex config can enable plugins, extra writable roots,
        # and network access. A routed task gets none of that: the boundary
        # is set here and nowhere else.
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox", SANDBOX[mode],
        "-c", "sandbox_workspace_write.network_access=false",
        "-c", "sandbox_workspace_write.writable_roots=[]",
        "--cd", cwd,
        "--skip-git-repo-check",
        "-c", 'model_reasoning_effort="%s"' % effort,
    ]
    if model:
        command += ["--model", model]
    return command + ["--output-last-message", out_file, "-"]


def run(
    mode: str,
    prompt_file: str,
    state_dir: Path,
    now: int,
    runner: Callable[..., Any] = subprocess.run,
) -> Tuple[int, str]:
    if mode not in SANDBOX:
        return 2, "codex-run: mode must be read or write"
    try:
        with open(prompt_file, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, ValueError) as exc:
        return 2, "codex-run: could not read the prompt file: %s" % exc

    nonce, task = split_grant(text)
    grant = grants.redeem(state_dir, nonce, now)
    if grant is None:
        return 2, NO_GRANT
    if grant["mode"] != mode:
        return 2, "codex-run: this grant is for a %s run, not a %s run" % (
            grant["mode"], mode,
        )
    if not task.strip():
        return 2, "codex-run: the task is empty"

    descriptor, out_file = tempfile.mkstemp(prefix="model-router-codex-", suffix=".txt")
    os.close(descriptor)
    try:
        try:
            completed = runner(
                # Everything that shapes the run comes from the grant the hook
                # wrote, never from the prompt text.
                build_command(mode, grant["effort"], grant["cwd"], out_file, grant["model"]),
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
