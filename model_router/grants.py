"""One-time grants that tie a Codex run to a redirect the hook approved.

Redirect eligibility is decided once, in the pre-launch hook. Without a
grant, nothing would stop the write-enabled worker from being launched
directly, or the wrapper from asking for the wrong sandbox. So the hook
writes a grant when it redirects, puts its nonce in the worker's prompt, and
the runner refuses to start Codex unless it can redeem that grant. The grant,
not the prompt and not the worker, decides the sandbox, effort, model, and
working directory.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from model_router.config import CODEX_EFFORTS, MODEL_NAME
from model_router.fsutil import atomic_write_text

# Long enough to cover the owner answering permission prompts for the worker.
GRANT_TTL_SECONDS = 30 * 60
MODES = ("read", "write")
NONCE_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _directory(state_dir: Path) -> Path:
    return Path(state_dir) / "grants"


def _load(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            grant = json.load(handle)
    except (OSError, ValueError):
        return None
    return grant if isinstance(grant, dict) else None


def _expired(grant: Optional[Dict[str, Any]], now: int) -> bool:
    if grant is None:
        return True
    issued_at = grant.get("issued_at")
    if isinstance(issued_at, bool) or not isinstance(issued_at, int):
        return True
    return now - issued_at > GRANT_TTL_SECONDS


def _prune(state_dir: Path, now: int) -> None:
    try:
        entries = list(_directory(state_dir).glob("*.json"))
    except OSError:
        return
    for path in entries:
        if _expired(_load(path), now):
            try:
                path.unlink()
            except OSError:
                pass


def issue(
    state_dir: Path, mode: str, effort: str, model: Optional[str], cwd: str, now: int
) -> str:
    """Record an approved redirect and return the nonce that redeems it."""
    import secrets  # Only needed on a redirect; keep it off the hook's fast path.

    _prune(state_dir, now)
    nonce = secrets.token_hex(16)
    grant = {
        "mode": mode, "effort": effort, "model": model,
        "cwd": cwd, "issued_at": int(now),
    }
    atomic_write_text(
        _directory(state_dir) / (nonce + ".json"),
        json.dumps(grant, sort_keys=True) + "\n",
    )
    return nonce


def redeem(state_dir: Path, nonce: Any, now: int) -> Optional[Dict[str, Any]]:
    """Consume a grant. Returns it, or None if it is missing, used, or bad."""
    # The nonce becomes a file name, so its shape is checked before any I/O.
    if not isinstance(nonce, str) or not NONCE_PATTERN.match(nonce):
        return None
    path = _directory(state_dir) / (nonce + ".json")
    grant = _load(path)
    try:
        # Consumed whatever happens next: a grant is good for one attempt.
        path.unlink()
    except OSError:
        return None
    if grant is None or _expired(grant, now):
        return None
    cwd, model = grant.get("cwd"), grant.get("model")
    if grant.get("mode") not in MODES or grant.get("effort") not in CODEX_EFFORTS:
        return None
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return None
    if model is not None and not (isinstance(model, str) and MODEL_NAME.match(model)):
        return None
    return grant
