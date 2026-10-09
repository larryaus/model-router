# Model Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude Code plugin that routes subagent launches across a Claude plan and a ChatGPT plan (through the Codex CLI), using local quota readings, a preference list per task category, and a balance tiebreaker.

**Architecture:** Pure functions do the thinking: quota readers normalize on-disk data, a classifier names the task category, and `decide()` picks a target. Thin hook adapters connect those to Claude Code (`PreToolUse` on the `Agent` tool, `UserPromptSubmit`, and the status line). Everything fails open: any error leaves the launch untouched.

**Tech Stack:** Python 3.9 standard library only at runtime, run as `/usr/bin/python3 -I`. `pytest` for tests, development only. JSONC config. Plain JSON and JSONL files for state. Packaged as a Claude Code plugin.

**Spec:** `docs/superpowers/specs/2026-10-09-model-router-design.md`. Read it before starting. Section numbers below (§) refer to it.

## Global Constraints

- Python 3.9: no `X | Y` unions, no `tomllib`, no `match`. Use `typing.List`, `Optional`, `Dict`, `Tuple`, `NamedTuple`.
- Runtime code imports the standard library only. `pytest` is the only development dependency.
- Every entry point runs as `/usr/bin/python3 -I`. Isolated mode does not put the script's directory on `sys.path`, so `bin/router` adds the repository root itself.
- Run tests with `.venv/bin/python -m pytest` from the repository root, never bare `pytest`.
- Never read, store, or forward credentials. Never open `~/.codex/auth.json` or the Keychain. No network calls.
- Fail open: a hook prints nothing and exits 0 on any error. Exit code 2 from a `PreToolUse` hook blocks the launch, so a hook must never exit 2.
- The decision log never holds prompt text or the launch description.
- The router alters launches only when the config file exists, validates, and sets `"mode": "enforce"`.
- Tests never read `~/.claude`, `~/.codex`, or the real config. Paths are injected through `Paths`.
- State files are written atomically (temporary file in the same directory, then rename) with owner-only permissions. The decision log is the one exception: it is appended with a single `O_APPEND` write per record, which is the atomic form for an append-only file.
- Changes to `~/.claude/settings.json` and to the owner's existing agents need the owner's confirmation at that step.
- Category names, in full: `plan`, `implement`, `review`, `explore`, `debug`, `default`.

## Additions to the spec's layout

These are small and stay inside the spec's design. They are listed so a reviewer is not surprised:

- `model_router/paths.py`: one place for file locations and environment overrides (§5, §6).
- `model_router/fsutil.py`: the atomic write helper (§6).
- `model_router/codex_run.py`: builds and runs the `codex exec` command for the workers, so the sandbox pinning in §3.5 is enforced and tested in code instead of living in an agent prompt.
- `.claude-plugin/marketplace.json`: needed to install the plugin from a local path.
- `Decision` gains a fifth field, `worker` (`"codex-read"`, `"codex-write"`, or `None`).
- Each decision record also stores `permission_mode`, `nested`, and `deny_rules`, so the shadow run can confirm those hook fields arrive (§10 phase 3).
- The prompt hook injects guidance only in `enforce` mode. In `shadow` mode nothing the model sees is changed.

## File map

| File | Responsibility |
|---|---|
| `model_router/fsutil.py` | `atomic_write_text` |
| `model_router/paths.py` | `Paths`, `from_env` |
| `model_router/quota/types.py` | `Window`, `PlanQuota`, small pure helpers |
| `model_router/quota/claude.py` | Claude snapshot: extract, write, read |
| `model_router/quota/codex.py` | Codex session-log reader |
| `model_router/quota/__init__.py` | `read_quotas(paths)` |
| `model_router/config.py` | JSONC load, defaults, validation, three load results |
| `model_router/classify.py` | Launch → category |
| `model_router/policy.py` | Levels, redirect eligibility, `decide()` |
| `model_router/decision_log.py` | Append and read `decisions.jsonl` |
| `model_router/state.py` | Per-session `state.json` |
| `model_router/hooks/permissions.py` | File-scoped deny-rule detection |
| `model_router/hooks/__init__.py` | `run_hook`: the fail-open wrapper |
| `model_router/hooks/pre_agent.py` | `PreToolUse` adapter |
| `model_router/hooks/statusline.py` | Status-line capture and passthrough |
| `model_router/hooks/prompt.py` | `UserPromptSubmit` adapter |
| `model_router/codex_run.py` | `codex exec` command builder and runner |
| `model_router/cli.py` | `status`, `explain`, `log`, `hook`, `statusline`, `codex-run` |
| `bin/router` | Entry script |
| `agents/codex-read.md`, `agents/codex-write.md` | Forwarding agents |
| `hooks/hooks.json`, `.claude-plugin/*.json` | Plugin wiring |
| `config.example.jsonc`, `README.md` | Owner-facing docs |

## Review Focus

These are the inputs the spec implies but does not spell out. Each has a test in the task that owns the code.

1. **Hook stdin that is empty, not JSON, or a JSON array.** Expected: exit 0, no output, launch unchanged. Tests in Task 11.
2. **Launch fields that are not strings** (`prompt: null`, a numeric `description`, a list for `subagent_type`). Expected: classified as `default`, no crash. Tests in Task 5 and Task 11.
3. **A state directory that cannot be written** (its path is blocked by a file). Expected: the hook returns nothing even in `enforce` mode, and the status line still relays its passthrough. Tests in Task 11 and Task 12.
4. **A config that is valid JSON but the wrong shape**: a top-level array, a trailing comma, a byte-order mark. Expected: the first two are invalid and inert; the third loads. Tests in Task 4.
5. **A quota window with no reset time, or a reset further away than the window is long** (clock skew). Expected: no division by zero, projection undefined, level from `used` alone. Tests in Task 6.

---

### Task 1: Project scaffold, atomic writes, and paths

**Files:**
- Create: `.gitignore`, `requirements-dev.txt`
- Create: `model_router/__init__.py`, `model_router/quota/__init__.py`, `model_router/hooks/__init__.py` (all empty for now)
- Create: `model_router/fsutil.py`, `model_router/paths.py`
- Create: `tests/__init__.py` (empty), `tests/conftest.py`, `tests/helpers.py`
- Test: `tests/test_fsutil.py`, `tests/test_paths.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `fsutil.atomic_write_text(path: Path, text: str) -> None`
  - `paths.Paths(config, state_dir, codex_sessions, claude_home, managed_settings)`, all `Path`, with read-only properties `claude_quota`, `state`, `log`
  - `paths.from_env(env: Mapping[str, str]) -> Paths`
  - `tests/helpers.py`: `NOW = 1_800_000_000`
  - `tests/conftest.py`: fixture `paths` (a `Paths` rooted in `tmp_path`)

- [ ] **Step 1: Create the scaffold**

```bash
cd /Users/Larry/GitHub/model-router
/usr/bin/python3 -m venv .venv
.venv/bin/pip install --quiet pytest
mkdir -p model_router/quota model_router/hooks tests bin
touch model_router/__init__.py model_router/quota/__init__.py model_router/hooks/__init__.py tests/__init__.py
printf '.venv/\n__pycache__/\n.pytest_cache/\n*.pyc\n' > .gitignore
printf 'pytest\n' > requirements-dev.txt
.venv/bin/python --version
```

Expected last line: `Python 3.9.6`

- [ ] **Step 2: Write the shared test helpers**

`tests/helpers.py`:

```python
"""Builders shared by the test suite. Later tasks append to this file."""

NOW = 1_800_000_000
```

`tests/conftest.py`:

```python
import pytest

from model_router.paths import Paths


@pytest.fixture
def paths(tmp_path):
    """File locations rooted in tmp_path, so no test touches real state."""
    return Paths(
        config=tmp_path / "config.jsonc",
        state_dir=tmp_path / "state",
        codex_sessions=tmp_path / "codex-sessions",
        claude_home=tmp_path / "claude-home",
        managed_settings=tmp_path / "managed-settings.json",
    )
```

- [ ] **Step 3: Write the failing tests**

`tests/test_fsutil.py`:

```python
import stat

from model_router.fsutil import atomic_write_text


def test_creates_parent_and_owner_only_file(tmp_path):
    target = tmp_path / "nested" / "file.json"
    atomic_write_text(target, "hello\n")
    assert target.read_text() == "hello\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_replaces_existing_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "file.json"
    atomic_write_text(target, "one")
    atomic_write_text(target, "two")
    assert target.read_text() == "two"
    assert [entry.name for entry in tmp_path.iterdir()] == ["file.json"]
```

`tests/test_paths.py`:

```python
from pathlib import Path

from model_router.paths import from_env


def test_defaults_live_under_home(tmp_path):
    paths = from_env({"HOME": str(tmp_path)})
    assert paths.config == tmp_path / ".config/model-router/config.jsonc"
    assert paths.state_dir == tmp_path / "Library/Caches/model-router"
    assert paths.claude_quota == paths.state_dir / "claude-quota.json"
    assert paths.state == paths.state_dir / "state.json"
    assert paths.log == paths.state_dir / "decisions.jsonl"
    assert paths.codex_sessions == tmp_path / ".codex/sessions"
    assert paths.claude_home == tmp_path / ".claude"


def test_environment_overrides(tmp_path):
    paths = from_env({
        "HOME": str(tmp_path),
        "MODEL_ROUTER_CONFIG": "/elsewhere/config.jsonc",
        "MODEL_ROUTER_STATE_DIR": "/elsewhere/state",
    })
    assert paths.config == Path("/elsewhere/config.jsonc")
    assert paths.log == Path("/elsewhere/state/decisions.jsonl")
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_fsutil.py tests/test_paths.py -q`
Expected: errors with `ModuleNotFoundError: No module named 'model_router.fsutil'` (and `model_router.paths`).

- [ ] **Step 5: Implement**

`model_router/fsutil.py`:

```python
"""File helpers shared by every module that writes state."""

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write text through a temp file in the same directory, owner-only."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(
        prefix="." + destination.name + ".",
        dir=str(destination.parent),
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(destination))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
```

`model_router/paths.py`:

```python
"""Where the router keeps its files, and how the environment overrides that."""

import os
from pathlib import Path
from typing import Mapping, NamedTuple

MANAGED_SETTINGS = "/Library/Application Support/ClaudeCode/managed-settings.json"


class Paths(NamedTuple):
    config: Path
    state_dir: Path
    codex_sessions: Path
    claude_home: Path
    managed_settings: Path

    @property
    def claude_quota(self) -> Path:
        return self.state_dir / "claude-quota.json"

    @property
    def state(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def log(self) -> Path:
        return self.state_dir / "decisions.jsonl"


def from_env(env: Mapping[str, str]) -> Paths:
    home = Path(env.get("HOME") or os.path.expanduser("~"))
    return Paths(
        config=Path(
            env.get("MODEL_ROUTER_CONFIG")
            or home / ".config/model-router/config.jsonc"
        ),
        state_dir=Path(
            env.get("MODEL_ROUTER_STATE_DIR")
            or home / "Library/Caches/model-router"
        ),
        codex_sessions=home / ".codex/sessions",
        claude_home=home / ".claude",
        managed_settings=Path(MANAGED_SETTINGS),
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_fsutil.py tests/test_paths.py -q`
Expected: `4 passed`

- [ ] **Step 7: Commit**

```bash
git add .gitignore requirements-dev.txt model_router tests
git commit -m "Add project scaffold, atomic writes, and paths"
```

---

### Task 2: Quota types and the Claude snapshot

**Files:**
- Create: `model_router/quota/types.py`, `model_router/quota/claude.py`
- Test: `tests/test_quota_claude.py`

**Interfaces:**
- Consumes: `fsutil.atomic_write_text`.
- Produces, in `quota/types.py`:
  - `FIVE_HOUR_MINUTES = 300`, `WEEK_MINUTES = 10080`
  - `Window(used_pct: float, window_minutes: int, resets_at: Optional[int])`
  - `PlanQuota(plan: str, windows: List[Window], captured_at: Optional[int], known: bool)`
  - `unknown(plan: str) -> PlanQuota`
  - `number(value) -> Optional[float]` (rejects bools, strings, NaN, infinity)
  - `effective_used_pct(window: Window, now: int) -> float` (0.0 once the reset has passed)
  - `window_label(window: Window) -> str` (`"5h"`, `"weekly"`, or `"<n>m"`)
  - `format_time(timestamp: int) -> str` (local time, for example `Thu 14:20`)
- Produces, in `quota/claude.py`:
  - `extract_rate_limits(payload) -> Dict[str, Dict[str, Any]]` (`{}` when nothing usable)
  - `write_snapshot(payload, path: Path, now: int) -> bool`
  - `read_claude_quota(path: Path) -> PlanQuota`

The status-line input from Claude Code carries `rate_limits.five_hour` and `rate_limits.seven_day`, each with `used_percentage` (0 to 100) and `resets_at` (epoch seconds). Early in a session `rate_limits` can be absent.

- [ ] **Step 1: Write the failing tests**

`tests/test_quota_claude.py`:

```python
import json

import pytest

from model_router.quota.claude import (
    extract_rate_limits,
    read_claude_quota,
    write_snapshot,
)
from model_router.quota.types import (
    PlanQuota,
    Window,
    effective_used_pct,
    unknown,
    window_label,
)

PAYLOAD = {
    "session_id": "secret-session",
    "transcript_path": "/private/path",
    "rate_limits": {
        "five_hour": {"used_percentage": 42.5, "resets_at": 1800001000},
        "seven_day": {"used_percentage": 7, "resets_at": 1800500000},
    },
}


def test_snapshot_keeps_only_quota_fields(tmp_path):
    target = tmp_path / "state" / "claude-quota.json"
    assert write_snapshot(PAYLOAD, target, 1800000000) is True
    assert json.loads(target.read_text()) == {
        "captured_at": 1800000000,
        "rate_limits": {
            "five_hour": {"used_percentage": 42.5, "resets_at": 1800001000},
            "seven_day": {"used_percentage": 7.0, "resets_at": 1800500000},
        },
    }


def test_snapshot_not_written_without_rate_limits(tmp_path):
    target = tmp_path / "claude-quota.json"
    assert write_snapshot({"session_id": "x"}, target, 1) is False
    assert not target.exists()


def test_read_round_trip(tmp_path):
    target = tmp_path / "claude-quota.json"
    write_snapshot(PAYLOAD, target, 1800000000)
    assert read_claude_quota(target) == PlanQuota(
        "claude",
        [Window(42.5, 300, 1800001000), Window(7.0, 10080, 1800500000)],
        1800000000,
        True,
    )


def test_missing_or_malformed_snapshot_is_unknown(tmp_path):
    assert read_claude_quota(tmp_path / "absent.json") == unknown("claude")
    bad = tmp_path / "bad.json"
    for text in ("{not json", "[]", '{"rate_limits": {}}'):
        bad.write_text(text)
        assert read_claude_quota(bad) == unknown("claude")


@pytest.mark.parametrize("value", [True, "50", -1, 101, float("nan"), None])
def test_invalid_percentages_are_dropped(value):
    payload = {"rate_limits": {"five_hour": {"used_percentage": value, "resets_at": 1}}}
    assert extract_rate_limits(payload) == {}


def test_missing_reset_time_is_kept_as_none():
    limits = extract_rate_limits({"rate_limits": {"seven_day": {"used_percentage": 5}}})
    assert limits == {"seven_day": {"used_percentage": 5.0, "resets_at": None}}


def test_passed_reset_counts_as_zero():
    assert effective_used_pct(Window(80.0, 300, 100), now=100) == 0.0
    assert effective_used_pct(Window(80.0, 300, 101), now=100) == 80.0
    assert effective_used_pct(Window(80.0, 300, None), now=100) == 80.0


def test_window_labels():
    assert window_label(Window(1.0, 300, None)) == "5h"
    assert window_label(Window(1.0, 10080, None)) == "weekly"
    assert window_label(Window(1.0, 60, None)) == "60m"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_quota_claude.py -q`
Expected: `ModuleNotFoundError: No module named 'model_router.quota.claude'`

- [ ] **Step 3: Implement**

`model_router/quota/types.py`:

```python
"""The one shape every quota reader produces."""

import math
import time
from typing import Any, List, NamedTuple, Optional

FIVE_HOUR_MINUTES = 300
WEEK_MINUTES = 10080


class Window(NamedTuple):
    used_pct: float
    window_minutes: int
    resets_at: Optional[int]


class PlanQuota(NamedTuple):
    plan: str
    windows: List[Window]
    captured_at: Optional[int]
    known: bool


def unknown(plan: str) -> PlanQuota:
    return PlanQuota(plan, [], None, False)


def number(value: Any) -> Optional[float]:
    """Return value as a finite float, or None for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def effective_used_pct(window: Window, now: int) -> float:
    """A window whose reset time has passed has started again at zero."""
    if window.resets_at is not None and window.resets_at <= now:
        return 0.0
    return window.used_pct


def window_label(window: Window) -> str:
    if window.window_minutes == FIVE_HOUR_MINUTES:
        return "5h"
    if window.window_minutes == WEEK_MINUTES:
        return "weekly"
    return "%dm" % window.window_minutes


def format_time(timestamp: int) -> str:
    return time.strftime("%a %H:%M", time.localtime(timestamp))
```

`model_router/quota/claude.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_quota_claude.py -q`
Expected: `13 passed`

- [ ] **Step 5: Commit**

```bash
git add model_router/quota tests/test_quota_claude.py
git commit -m "Add quota types and the Claude snapshot reader"
```

---

### Task 3: Codex quota reader

**Files:**
- Create: `model_router/quota/codex.py`
- Modify: `model_router/quota/__init__.py` (currently empty)
- Modify: `tests/helpers.py` (append)
- Test: `tests/test_quota_codex.py`

**Interfaces:**
- Consumes: `quota.types` (`Window`, `PlanQuota`, `number`, `unknown`), `quota.claude.read_claude_quota`, `quota.claude.write_snapshot`, `paths.Paths`.
- Produces:
  - `quota.codex.read_codex_quota(sessions_dir: Path, max_files: int = 20, tail_bytes: int = 262144) -> PlanQuota`
  - `quota.read_quotas(paths: Paths) -> Dict[str, PlanQuota]` with keys `"claude"` and `"codex"`
  - `tests/helpers.py`: `seed_claude(paths, five, week, now=NOW)` and `seed_codex(paths, five, week, now=NOW)`, which write quota files where each window is 10% elapsed

Codex appends one JSON object per line to `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. The lines that matter look like this (verified against a real file on this machine):

```json
{"timestamp":"2026-10-07T01:28:13.535Z","type":"event_msg","payload":{"type":"token_count","info":{},"rate_limits":{"primary":{"used_percent":59.0,"window_minutes":300,"resets_at":1791338519},"secondary":{"used_percent":14.0,"window_minutes":10080,"resets_at":1791802257}}}}
```

Either window can be `null`. Files can be several hundred kilobytes, so only the tail is read.

- [ ] **Step 1: Append the seeding helpers**

Append to `tests/helpers.py`:

```python
import json
import os

from model_router.quota.claude import write_snapshot


def _reset(now, minutes, elapsed=0.1):
    """A reset time that leaves the window `elapsed` of the way through."""
    return now + int(round(minutes * 60 * (1 - elapsed)))


def seed_claude(paths, five, week, now=NOW):
    write_snapshot(
        {
            "rate_limits": {
                "five_hour": {"used_percentage": five, "resets_at": _reset(now, 300)},
                "seven_day": {"used_percentage": week, "resets_at": _reset(now, 10080)},
            }
        },
        paths.claude_quota,
        now,
    )


def codex_line(primary, secondary, timestamp="2026-10-07T01:28:13.535Z"):
    return json.dumps({
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": None,
            "rate_limits": {"primary": primary, "secondary": secondary},
        },
    })


def write_rollout(sessions_dir, name, lines, mtime):
    folder = sessions_dir / "2026" / "10" / "07"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("rollout-%s.jsonl" % name)
    path.write_text("".join(line + "\n" for line in lines))
    os.utime(str(path), (mtime, mtime))
    return path


def seed_codex(paths, five, week, now=NOW):
    write_rollout(
        paths.codex_sessions,
        "seed",
        [codex_line(
            {"used_percent": five, "window_minutes": 300, "resets_at": _reset(now, 300)},
            {"used_percent": week, "window_minutes": 10080, "resets_at": _reset(now, 10080)},
        )],
        mtime=now,
    )
```

- [ ] **Step 2: Write the failing tests**

`tests/test_quota_codex.py`:

```python
from datetime import datetime, timezone

from model_router.quota import read_quotas
from model_router.quota.codex import read_codex_quota
from model_router.quota.types import Window, unknown
from tests.helpers import NOW, codex_line, seed_claude, seed_codex, write_rollout

P = {"used_percent": 59.0, "window_minutes": 300, "resets_at": 1791338519}
S = {"used_percent": 14.0, "window_minutes": 10080, "resets_at": 1791802257}


def test_reads_the_last_reading_of_the_newest_file(tmp_path):
    write_rollout(tmp_path, "old", [codex_line(dict(P, used_percent=1.0), S)], mtime=1000)
    write_rollout(
        tmp_path,
        "new",
        ['{"type":"other"}', codex_line(dict(P, used_percent=20.0), S),
         codex_line(P, S), '{"type":"tail"}'],
        mtime=2000,
    )
    quota = read_codex_quota(tmp_path)
    assert quota.plan == "codex" and quota.known is True
    assert quota.windows == [Window(59.0, 300, 1791338519), Window(14.0, 10080, 1791802257)]
    expected = datetime(2026, 10, 7, 1, 28, 13, 535000, tzinfo=timezone.utc)
    assert quota.captured_at == int(expected.timestamp())


def test_falls_back_to_an_older_file(tmp_path):
    write_rollout(tmp_path, "old", [codex_line(P, S)], mtime=1000)
    write_rollout(tmp_path, "new", ['{"type":"other"}'], mtime=2000)
    assert read_codex_quota(tmp_path).windows[0] == Window(59.0, 300, 1791338519)


def test_skips_lines_that_do_not_parse(tmp_path):
    write_rollout(
        tmp_path,
        "new",
        [codex_line(P, S), '{"payload": {"rate_limits": ', 'garbage "rate_limits"'],
        mtime=2000,
    )
    assert read_codex_quota(tmp_path).known is True


def test_reads_only_the_tail(tmp_path):
    filler = ['{"type":"filler","pad":"%s"}' % ("x" * 200)] * 50
    write_rollout(tmp_path, "big", [codex_line(P, S)] + filler, mtime=2000)
    assert read_codex_quota(tmp_path, tail_bytes=1024) == unknown("codex")
    assert read_codex_quota(tmp_path).known is True


def test_a_null_window_is_skipped(tmp_path):
    write_rollout(tmp_path, "new", [codex_line(None, S)], mtime=2000)
    assert read_codex_quota(tmp_path).windows == [Window(14.0, 10080, 1791802257)]


def test_missing_or_empty_directory_is_unknown(tmp_path):
    assert read_codex_quota(tmp_path / "absent") == unknown("codex")
    assert read_codex_quota(tmp_path) == unknown("codex")


def test_max_files_limits_the_search(tmp_path):
    write_rollout(tmp_path, "a", [codex_line(P, S)], mtime=1000)
    write_rollout(tmp_path, "b", ['{"type":"other"}'], mtime=2000)
    write_rollout(tmp_path, "c", ['{"type":"other"}'], mtime=3000)
    assert read_codex_quota(tmp_path, max_files=2) == unknown("codex")
    assert read_codex_quota(tmp_path, max_files=3).known is True


def test_read_quotas_returns_both_plans(paths):
    seed_claude(paths, five=30, week=10)
    seed_codex(paths, five=5, week=2)
    quotas = read_quotas(paths)
    assert sorted(quotas) == ["claude", "codex"]
    assert quotas["claude"].windows[0].used_pct == 30.0
    assert quotas["codex"].windows[0].used_pct == 5.0
    assert quotas["claude"].captured_at == NOW
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_quota_codex.py -q`
Expected: `ImportError: cannot import name 'read_quotas' from 'model_router.quota'`

- [ ] **Step 4: Implement**

`model_router/quota/codex.py`:

```python
"""ChatGPT plan quota, read from the Codex CLI's session logs.

Codex records its rate limits in the session log each time it runs. This
reader never touches Codex credentials; it only reads those log lines.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from model_router.quota.types import PlanQuota, Window, number, unknown

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
                return PlanQuota("codex", windows, _captured_at(record, mtime), True)
    return unknown("codex")
```

`model_router/quota/__init__.py`:

```python
"""Quota readers. `read_quotas` is the one call the rest of the router uses."""

from typing import Dict

from model_router.paths import Paths
from model_router.quota.claude import read_claude_quota
from model_router.quota.codex import read_codex_quota
from model_router.quota.types import PlanQuota


def read_quotas(paths: Paths) -> Dict[str, PlanQuota]:
    return {
        "claude": read_claude_quota(paths.claude_quota),
        "codex": read_codex_quota(paths.codex_sessions),
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_quota_codex.py -q`
Expected: `8 passed`

- [ ] **Step 6: Commit**

```bash
git add model_router/quota tests/helpers.py tests/test_quota_codex.py
git commit -m "Add the Codex quota reader"
```

---

### Task 4: Configuration

**Files:**
- Create: `model_router/config.py`
- Modify: `tests/helpers.py` (append)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces, in `config.py`:
  - Constants: `CATEGORIES`, `KEYWORD_CATEGORIES` (the keyword test order: review, debug, plan, explore, implement), `MODES`, `PLANS`, `WEIGHTS`, `CLAUDE_MODELS`, `CODEX_EFFORTS`, `PERMISSION_MODES`, `DEFAULTS`
  - `Target(name, plan, model, effort, weight)`; `model` is `None` for Codex targets and `effort` is `None` for Claude targets
  - `Config(mode, targets, preferences, conserve, critical, exhausted, warmup, overused, underused, agent_categories, keywords, passthrough, redirectable, readonly_agents, write_categories, read_redirect_modes, write_redirect_modes, statusline_passthrough)`
  - `ConfigResult(status, config, errors)` where `status` is `"valid"`, `"absent"`, or `"invalid"`, and `config` is `None` exactly when `status == "invalid"`
  - `strip_comments(text: str) -> str`
  - `build_config(raw) -> Tuple[Optional[Config], List[str]]`
  - `load_config(path: Path) -> ConfigResult`
  - `raw_statusline_passthrough(path: Path) -> Optional[str]`
  - `tests/helpers.py`: `default_config(**overrides) -> Config`

Merge rule: a dict-valued key in the file merges one level deep over its default (the file's entries win, the default's other entries stay). List-valued and scalar keys replace the default outright.

- [ ] **Step 1: Append the config helper**

Append to `tests/helpers.py`:

```python
from model_router.config import build_config


def default_config(**overrides):
    """The built-in defaults, with top-level keys overridden."""
    config, errors = build_config(overrides)
    assert config is not None, errors
    return config
```

- [ ] **Step 2: Write the failing tests**

`tests/test_config.py`:

```python
import pytest

from model_router.config import (
    load_config,
    raw_statusline_passthrough,
    strip_comments,
)
from tests.helpers import default_config


def write(tmp_path, text, encoding="utf-8"):
    path = tmp_path / "config.jsonc"
    path.write_text(text, encoding=encoding)
    return path


def test_strip_comments_respects_strings():
    text = '{\n  // line\n  "a": "http://x", /* block */ "b": "say \\"//hi\\""\n}'
    assert strip_comments(text) == '{\n  \n  "a": "http://x",  "b": "say \\"//hi\\""\n}'


def test_absent_file_gives_shadow_defaults(tmp_path):
    result = load_config(tmp_path / "absent.jsonc")
    assert result.status == "absent"
    assert result.config.mode == "shadow"
    assert result.errors == []


def test_empty_object_is_valid_and_defaults_to_shadow(tmp_path):
    result = load_config(write(tmp_path, "{}"))
    assert result.status == "valid"
    assert result.config == default_config()
    assert result.config.mode == "shadow"


def test_enforce_must_be_asked_for(tmp_path):
    result = load_config(write(tmp_path, '{ "mode": "enforce" } // go live'))
    assert result.status == "valid" and result.config.mode == "enforce"


def test_defaults_match_the_spec():
    config = default_config()
    assert config.preferences["implement"] == ["codex-deep", "sonnet", "codex"]
    assert config.targets["codex-deep"].effort == "xhigh"
    assert config.targets["codex-deep"].model is None
    assert config.targets["opus"].model == "opus"
    assert (config.conserve, config.critical, config.exhausted) == (70, 90, 98)
    assert config.write_redirect_modes == ["acceptEdits", "bypassPermissions"]
    assert config.statusline_passthrough is None


def test_dict_keys_merge_and_list_keys_replace(tmp_path):
    result = load_config(write(tmp_path, """{
      "thresholds": {"conserve": 60},
      "preferences": {"plan": ["sonnet"]},
      "passthrough": ["only-this"]
    }"""))
    assert result.status == "valid"
    assert (result.config.conserve, result.config.critical) == (60, 90)
    assert result.config.preferences["plan"] == ["sonnet"]
    assert result.config.preferences["default"] == ["sonnet", "codex"]
    assert result.config.passthrough == ["only-this"]


def test_byte_order_mark_is_tolerated(tmp_path):
    result = load_config(write(tmp_path, '{"mode": "off"}', encoding="utf-8-sig"))
    assert result.status == "valid" and result.config.mode == "off"


@pytest.mark.parametrize("text,fragment", [
    ("{not json", "not valid JSON"),
    ("", "not valid JSON"),
    ("[]", "top level"),
    ('{"mode": "off",}', "not valid JSON"),
    ('{"mdoe": "shadow"}', "mdoe: unknown key"),
    ('{"mode": "on"}', "mode:"),
    ('{"preferences": {"plan": ["nope"]}}', "unknown target 'nope'"),
    ('{"preferences": {"plan": []}}', "preferences.plan: must not be empty"),
    ('{"preferences": {"chores": ["sonnet"]}}', "preferences.chores: unknown key"),
    ('{"thresholds": {"conserve": 95}}', "conserve < critical < exhausted"),
    ('{"thresholds": {"extra": 1}}', "thresholds.extra: unknown key"),
    ('{"balance": {"underused": 90}}', "underused must be below overused"),
    ('{"projection": {"warmup": 1}}', "projection.warmup"),
    ('{"targets": {"x": {"plan": "claude", "model": "gpt", "weight": "light"}}}', "targets.x.model"),
    ('{"targets": {"x": {"plan": "codex", "effort": "max", "weight": "light"}}}', "targets.x.effort"),
    ('{"targets": {"x": {"plan": "claude", "model": "opus", "weight": "huge"}}}', "targets.x.weight"),
    ('{"targets": {"x": {"plan": "gemini", "weight": "light"}}}', "targets.x.plan"),
    ('{"read_redirect_modes": ["plan"]}', "plan may not be listed"),
    ('{"write_redirect_modes": ["dontAsk"]}', "must be a subset"),
    ('{"write_redirect_modes": ["sometimes"]}', "unknown value 'sometimes'"),
    ('{"write_categories": ["chores"]}', "unknown value 'chores'"),
    ('{"agent_categories": {"Explore": "chores"}}', "agent_categories.Explore"),
    ('{"passthrough": "codex-review"}', "passthrough: expected a list"),
    ('{"statusline": {"passthrough": 5}}', "statusline.passthrough"),
])
def test_invalid_config_is_inert(tmp_path, text, fragment):
    result = load_config(write(tmp_path, text))
    assert result.status == "invalid"
    assert result.config is None
    assert any(fragment in error for error in result.errors), result.errors


def test_statusline_passthrough_survives_an_invalid_config(tmp_path):
    path = write(tmp_path, '{"mdoe": "x", "statusline": {"passthrough": "echo hi"}}')
    assert load_config(path).status == "invalid"
    assert raw_statusline_passthrough(path) == "echo hi"


def test_statusline_passthrough_absent_or_unreadable(tmp_path):
    assert raw_statusline_passthrough(tmp_path / "absent.jsonc") is None
    assert raw_statusline_passthrough(write(tmp_path, "{not json")) is None
    assert raw_statusline_passthrough(write(tmp_path, "{}")) is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: `ModuleNotFoundError: No module named 'model_router.config'`

- [ ] **Step 4: Implement**

`model_router/config.py`:

```python
"""Load and validate the router configuration.

The loader has exactly three outcomes, and never a partial one:

  valid    the file exists and passed validation
  absent   no file; built-in defaults, which are in shadow mode
  invalid  syntax error, failed validation, or an unknown key; the router
           goes inert and alters nothing
"""

import json
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

CATEGORIES = ("plan", "implement", "review", "explore", "debug", "default")
# Also the order in which the classifier tests keyword categories.
KEYWORD_CATEGORIES = ("review", "debug", "plan", "explore", "implement")
MODES = ("enforce", "shadow", "off")
PLANS = ("claude", "codex")
WEIGHTS = ("light", "medium", "heavy")
CLAUDE_MODELS = ("opus", "sonnet", "haiku", "fable")
CODEX_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
PERMISSION_MODES = (
    "default", "plan", "acceptEdits", "auto", "dontAsk", "bypassPermissions",
)

DEFAULTS: Dict[str, Any] = {
    "mode": "shadow",
    "targets": {
        "opus": {"plan": "claude", "model": "opus", "weight": "heavy"},
        "sonnet": {"plan": "claude", "model": "sonnet", "weight": "medium"},
        "haiku": {"plan": "claude", "model": "haiku", "weight": "light"},
        "codex-deep": {"plan": "codex", "effort": "xhigh", "weight": "heavy"},
        "codex": {"plan": "codex", "effort": "medium", "weight": "medium"},
    },
    "preferences": {
        "plan": ["opus", "codex-deep", "sonnet"],
        "implement": ["codex-deep", "sonnet", "codex"],
        "review": ["codex-deep", "opus", "sonnet"],
        "explore": ["haiku", "codex", "sonnet"],
        "debug": ["opus", "codex-deep", "sonnet"],
        "default": ["sonnet", "codex"],
    },
    "thresholds": {"conserve": 70, "critical": 90, "exhausted": 98},
    "projection": {"warmup": 0.15},
    "balance": {"overused": 85, "underused": 60},
    "agent_categories": {"Explore": "explore", "explorer": "explore", "Plan": "plan"},
    "keywords": {
        "review": ["review", "audit", "critique"],
        "debug": ["debug", "root cause", "failing", "flaky", "stack trace"],
        "plan": ["plan", "design", "architecture"],
        "explore": ["find", "locate", "search", "where is", "trace how"],
        "implement": ["implement", "add", "build", "refactor", "fix", "write"],
    },
    "passthrough": [
        "codex-review", "design-review", "implementer",
        "statusline-setup", "claude-code-guide",
    ],
    "redirectable": ["general-purpose", "claude", "Explore", "Plan", "explorer"],
    "readonly_agents": ["Explore", "Plan", "explorer"],
    "write_categories": ["implement", "debug", "default"],
    "read_redirect_modes": ["default", "auto", "acceptEdits", "bypassPermissions"],
    "write_redirect_modes": ["acceptEdits", "bypassPermissions"],
    "statusline": {"passthrough": None},
}

_MERGED_KEYS = (
    "targets", "preferences", "thresholds", "projection", "balance",
    "agent_categories", "keywords", "statusline",
)


class Target(NamedTuple):
    name: str
    plan: str
    model: Optional[str]
    effort: Optional[str]
    weight: str


class Config(NamedTuple):
    mode: str
    targets: Dict[str, Target]
    preferences: Dict[str, List[str]]
    conserve: float
    critical: float
    exhausted: float
    warmup: float
    overused: float
    underused: float
    agent_categories: Dict[str, str]
    keywords: Dict[str, List[str]]
    passthrough: List[str]
    redirectable: List[str]
    readonly_agents: List[str]
    write_categories: List[str]
    read_redirect_modes: List[str]
    write_redirect_modes: List[str]
    statusline_passthrough: Optional[str]


class ConfigResult(NamedTuple):
    status: str
    config: Optional[Config]
    errors: List[str]


def strip_comments(text: str) -> str:
    """Remove // and /* */ comments, leaving string contents alone."""
    out: List[str] = []
    index, length = 0, len(text)
    in_string = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end == -1 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _str_list(value: Any, path: str, errors: List[str], allowed=None) -> List[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        errors.append("%s: expected a list of non-empty strings" % path)
        return []
    if allowed is not None:
        for item in value:
            if item not in allowed:
                errors.append("%s: unknown value %r" % (path, item))
    return list(value)


def _fixed_dict(value: Any, path: str, keys, errors: List[str]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        errors.append("%s: expected an object" % path)
        return {}
    for key in value:
        if key not in keys:
            errors.append("%s.%s: unknown key" % (path, key))
    return value


def _merged(raw: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        if key not in raw:
            merged[key] = default
        elif key in _MERGED_KEYS and isinstance(raw[key], dict):
            combined = dict(default)
            combined.update(raw[key])
            merged[key] = combined
        else:
            merged[key] = raw[key]
    return merged


def _targets(value: Any, errors: List[str]) -> Dict[str, Target]:
    targets: Dict[str, Target] = {}
    if not isinstance(value, dict) or not value:
        errors.append("targets: expected a non-empty object")
        return targets
    for name, raw in value.items():
        path = "targets.%s" % name
        spec = _fixed_dict(raw, path, ("plan", "model", "effort", "weight"), errors)
        plan, weight = spec.get("plan"), spec.get("weight")
        model, effort = spec.get("model"), spec.get("effort")
        if plan not in PLANS:
            errors.append("%s.plan: expected one of %s" % (path, ", ".join(PLANS)))
        if weight not in WEIGHTS:
            errors.append("%s.weight: expected one of %s" % (path, ", ".join(WEIGHTS)))
        if plan == "claude" and model not in CLAUDE_MODELS:
            errors.append(
                "%s.model: expected one of %s" % (path, ", ".join(CLAUDE_MODELS))
            )
        if plan == "codex" and effort not in CODEX_EFFORTS:
            errors.append(
                "%s.effort: expected one of %s" % (path, ", ".join(CODEX_EFFORTS))
            )
        targets[name] = Target(
            name,
            plan,
            model if plan == "claude" else None,
            effort if plan == "codex" else None,
            weight,
        )
    return targets


def _preferences(value: Any, targets, errors: List[str]) -> Dict[str, List[str]]:
    raw = _fixed_dict(value, "preferences", CATEGORIES, errors)
    preferences: Dict[str, List[str]] = {}
    for category in CATEGORIES:
        path = "preferences.%s" % category
        entry = raw.get(category)
        if isinstance(entry, list) and not entry:
            errors.append("%s: must not be empty" % path)
            preferences[category] = []
            continue
        names = _str_list(entry, path, errors)
        for index, name in enumerate(names):
            if name not in targets:
                errors.append("%s[%d]: unknown target %r" % (path, index, name))
        preferences[category] = names
    return preferences


def build_config(raw: Any) -> Tuple[Optional[Config], List[str]]:
    """Validate a parsed config. Returns (config, []) or (None, errors)."""
    if not isinstance(raw, dict):
        return None, ["config: expected a JSON object at the top level"]
    errors: List[str] = []
    for key in raw:
        if key not in DEFAULTS:
            errors.append("%s: unknown key" % key)
    data = _merged(raw)

    mode = data["mode"]
    if mode not in MODES:
        errors.append("mode: expected one of %s" % ", ".join(MODES))

    targets = _targets(data["targets"], errors)
    preferences = _preferences(data["preferences"], targets, errors)

    thresholds = _fixed_dict(
        data["thresholds"], "thresholds", ("conserve", "critical", "exhausted"), errors
    )
    levels = [thresholds.get(key) for key in ("conserve", "critical", "exhausted")]
    if not all(_is_number(value) and 0 < value <= 100 for value in levels):
        errors.append("thresholds: each value must be a number above 0 and at most 100")
    elif not levels[0] < levels[1] < levels[2]:
        errors.append("thresholds: expected conserve < critical < exhausted")

    projection = _fixed_dict(data["projection"], "projection", ("warmup",), errors)
    warmup = projection.get("warmup")
    if not _is_number(warmup) or not 0 <= warmup < 1:
        errors.append("projection.warmup: expected a number from 0 up to but not including 1")

    balance = _fixed_dict(data["balance"], "balance", ("overused", "underused"), errors)
    overused, underused = balance.get("overused"), balance.get("underused")
    if not _is_number(overused) or not _is_number(underused):
        errors.append("balance: overused and underused must be numbers")
    elif not underused < overused:
        errors.append("balance: underused must be below overused")

    agent_categories: Dict[str, str] = {}
    raw_agents = data["agent_categories"]
    if not isinstance(raw_agents, dict):
        errors.append("agent_categories: expected an object")
    else:
        for agent, category in raw_agents.items():
            if category not in CATEGORIES:
                errors.append(
                    "agent_categories.%s: expected one of %s"
                    % (agent, ", ".join(CATEGORIES))
                )
            agent_categories[agent] = category

    raw_keywords = _fixed_dict(data["keywords"], "keywords", KEYWORD_CATEGORIES, errors)
    keywords = {
        category: _str_list(raw_keywords.get(category, []), "keywords.%s" % category, errors)
        for category in KEYWORD_CATEGORIES
    }

    passthrough = _str_list(data["passthrough"], "passthrough", errors)
    redirectable = _str_list(data["redirectable"], "redirectable", errors)
    readonly_agents = _str_list(data["readonly_agents"], "readonly_agents", errors)
    write_categories = _str_list(
        data["write_categories"], "write_categories", errors, allowed=CATEGORIES
    )
    read_modes = _str_list(
        data["read_redirect_modes"], "read_redirect_modes", errors,
        allowed=PERMISSION_MODES,
    )
    write_modes = _str_list(
        data["write_redirect_modes"], "write_redirect_modes", errors,
        allowed=PERMISSION_MODES,
    )
    for name, modes in (
        ("read_redirect_modes", read_modes),
        ("write_redirect_modes", write_modes),
    ):
        if "plan" in modes:
            errors.append("%s: plan may not be listed" % name)
    if not set(write_modes) <= set(read_modes):
        errors.append("write_redirect_modes: must be a subset of read_redirect_modes")

    statusline = _fixed_dict(data["statusline"], "statusline", ("passthrough",), errors)
    passthrough_command = statusline.get("passthrough")
    if passthrough_command is not None and not (
        isinstance(passthrough_command, str) and passthrough_command
    ):
        errors.append("statusline.passthrough: expected null or a non-empty string")

    if errors:
        return None, errors
    return (
        Config(
            mode=mode,
            targets=targets,
            preferences=preferences,
            conserve=levels[0],
            critical=levels[1],
            exhausted=levels[2],
            warmup=warmup,
            overused=overused,
            underused=underused,
            agent_categories=agent_categories,
            keywords=keywords,
            passthrough=passthrough,
            redirectable=redirectable,
            readonly_agents=readonly_agents,
            write_categories=write_categories,
            read_redirect_modes=read_modes,
            write_redirect_modes=write_modes,
            statusline_passthrough=passthrough_command,
        ),
        [],
    )


def _parse_file(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.loads(strip_comments(handle.read()))


def load_config(path: Path) -> ConfigResult:
    try:
        raw = _parse_file(path)
    except FileNotFoundError:
        config, errors = build_config({})
        return ConfigResult("absent", config, errors)
    except OSError as exc:
        return ConfigResult("invalid", None, ["config: could not read %s: %s" % (path, exc)])
    except ValueError as exc:
        return ConfigResult("invalid", None, ["config: not valid JSON: %s" % exc])
    config, errors = build_config(raw)
    if config is None:
        return ConfigResult("invalid", None, errors)
    return ConfigResult("valid", config, [])


def raw_statusline_passthrough(path: Path) -> Optional[str]:
    """Best-effort read of statusline.passthrough, ignoring validation.

    The status line must keep relaying to the owner's previous command even
    while the rest of the config is broken.
    """
    try:
        value = _parse_file(path)["statusline"]["passthrough"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return value if isinstance(value, str) and value else None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: `33 passed`

- [ ] **Step 6: Commit**

```bash
git add model_router/config.py tests/helpers.py tests/test_config.py
git commit -m "Add config loading with three load results"
```

---

### Task 5: Classifier

**Files:**
- Create: `model_router/classify.py`
- Test: `tests/test_classify.py`

**Interfaces:**
- Consumes: `config.Config`, `config.KEYWORD_CATEGORIES`.
- Produces:
  - `Classification(category: str, source: str, override: Optional[str])`. `source` is `"tag"`, `"agent_type"`, `"keyword"`, or `"default"`. `override` is the text inside `[route:...]`, or `None`.
  - `classify(tool_input: Dict[str, Any], config: Config) -> Classification`

A tag sets `override` and `source = "tag"`, and the category is still worked out from the remaining rules, because the policy needs it to choose the read or write worker (§3.2, §4.4).

- [ ] **Step 1: Write the failing tests**

`tests/test_classify.py`:

```python
from model_router.classify import Classification, classify
from tests.helpers import default_config

CONFIG = default_config()


def run(description="", prompt="", subagent_type="general-purpose"):
    return classify(
        {"description": description, "prompt": prompt, "subagent_type": subagent_type},
        CONFIG,
    )


def test_agent_type_wins_over_keywords():
    assert run("Review the auth module", subagent_type="Explore") == Classification(
        "explore", "agent_type", None
    )


def test_keyword_in_description():
    assert run("Review the auth module") == Classification("review", "keyword", None)


def test_keyword_in_prompt_when_description_has_none():
    assert run("Auth module", "Find where tokens are stored").category == "explore"


def test_description_is_checked_before_prompt():
    assert run("Implement the cache", "then review it").category == "implement"


def test_categories_are_tested_in_a_fixed_order():
    assert run("debug and review the handler").category == "review"


def test_keywords_match_whole_words_only():
    assert run("Prefix the address list").category == "default"


def test_phrase_keywords_match():
    assert run("Tell me the root cause").category == "debug"


def test_prompt_is_scanned_for_500_characters_only():
    assert run("", ("x " * 260) + "review").category == "default"


def test_tag_sets_override_and_keeps_the_category():
    result = run("Cache work", "[route:opus] Implement the cache")
    assert result == Classification("implement", "tag", "opus")


def test_keep_tag():
    assert run("Cache work", "Implement it [route:keep]").override == "keep"


def test_non_string_fields_do_not_crash():
    result = classify({"description": 5, "prompt": None, "subagent_type": ["x"]}, CONFIG)
    assert result == Classification("default", "default", None)


def test_missing_fields_default():
    assert classify({}, CONFIG) == Classification("default", "default", None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_classify.py -q`
Expected: `ModuleNotFoundError: No module named 'model_router.classify'`

- [ ] **Step 3: Implement**

`model_router/classify.py`:

```python
"""Name the category of a subagent launch from cheap, deterministic signals."""

import re
from typing import Any, Dict, NamedTuple, Optional, Tuple

from model_router.config import KEYWORD_CATEGORIES, Config

ROUTE_TAG = re.compile(r"\[route:([A-Za-z0-9_-]+)\]")
PROMPT_SCAN_CHARS = 500


class Classification(NamedTuple):
    category: str
    source: str
    override: Optional[str]


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _has_keyword(text: str, keyword: str) -> bool:
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(keyword) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _category(
    agent_type: str, description: str, prompt: str, config: Config
) -> Tuple[str, str]:
    if agent_type in config.agent_categories:
        return config.agent_categories[agent_type], "agent_type"
    for text in (description, prompt[:PROMPT_SCAN_CHARS]):
        for category in KEYWORD_CATEGORIES:
            if any(_has_keyword(text, word) for word in config.keywords[category]):
                return category, "keyword"
    return "default", "default"


def classify(tool_input: Dict[str, Any], config: Config) -> Classification:
    prompt = _text(tool_input.get("prompt"))
    match = ROUTE_TAG.search(prompt)
    override = match.group(1) if match else None
    category, source = _category(
        _text(tool_input.get("subagent_type")),
        _text(tool_input.get("description")),
        ROUTE_TAG.sub("", prompt),
        config,
    )
    return Classification(category, "tag" if override else source, override)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_classify.py -q`
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add model_router/classify.py tests/test_classify.py
git commit -m "Add the launch classifier"
```

---

### Task 6: Policy, part 1: levels and projection

**Files:**
- Create: `model_router/policy.py`
- Modify: `tests/helpers.py` (append)
- Test: `tests/test_policy_levels.py`

**Interfaces:**
- Consumes: `config.Config`, `quota.types` (`Window`, `PlanQuota`, `WEEK_MINUTES`, `effective_used_pct`).
- Produces, in `policy.py`:
  - `LEVEL_ORDER = ("normal", "conserve", "critical", "exhausted")`
  - `ALLOWED_WEIGHTS`: level → tuple of allowed weights
  - `projected_pct(window: Window, now: int, warmup: float) -> Optional[float]`
  - `window_level(window: Window, now: int, config: Config) -> str`
  - `plan_level(quota: PlanQuota, now: int, config: Config) -> str`
  - `weekly_projection(quota: PlanQuota, now: int, config: Config) -> Optional[float]`
  - `tests/helpers.py`: `window(used, minutes=300, elapsed=0.1)` and `quota(plan, five=0, week=0, five_elapsed=0.1, week_elapsed=0.1)`

The helpers default to 10% elapsed, which is below the 15% warmup, so the projection is undefined and `used` alone sets the level. Tests about projection pass `elapsed` explicitly.

- [ ] **Step 1: Append the quota builders**

Append to `tests/helpers.py`:

```python
from model_router.quota.types import PlanQuota, Window


def window(used, minutes=300, elapsed=0.1):
    return Window(float(used), minutes, _reset(NOW, minutes, elapsed))


def quota(plan, five=0, week=0, five_elapsed=0.1, week_elapsed=0.1):
    return PlanQuota(
        plan,
        [window(five, 300, five_elapsed), window(week, 10080, week_elapsed)],
        NOW,
        True,
    )
```

- [ ] **Step 2: Write the failing tests**

`tests/test_policy_levels.py`:

```python
import pytest

from model_router.policy import (
    plan_level,
    projected_pct,
    weekly_projection,
    window_level,
)
from model_router.quota.types import Window, unknown
from tests.helpers import NOW, default_config, quota, window

CONFIG = default_config()


@pytest.mark.parametrize("used,expected", [
    (0, "normal"), (69.9, "normal"),
    (70, "conserve"), (89.9, "conserve"),
    (90, "critical"), (97.9, "critical"),
    (98, "exhausted"), (100, "exhausted"),
])
def test_threshold_boundaries(used, expected):
    assert window_level(window(used), NOW, CONFIG) == expected


def test_a_passed_reset_counts_as_zero():
    assert window_level(Window(99.0, 300, NOW - 1), NOW, CONFIG) == "normal"
    assert window_level(Window(99.0, 300, NOW), NOW, CONFIG) == "normal"


def test_projection_is_undefined_before_warmup():
    assert projected_pct(window(14, elapsed=0.14), NOW, 0.15) is None


def test_projection_after_warmup():
    assert projected_pct(window(30, elapsed=0.5), NOW, 0.15) == pytest.approx(60.0)


def test_projection_reaching_100_means_conserve():
    assert window_level(window(50, elapsed=0.5), NOW, CONFIG) == "conserve"
    assert window_level(window(49, elapsed=0.5), NOW, CONFIG) == "normal"


def test_projection_never_divides_by_zero():
    no_reset = Window(50.0, 300, None)
    at_window_start = Window(50.0, 300, NOW + 300 * 60)
    reset_beyond_window = Window(50.0, 300, NOW + 999_999)
    zero_length = Window(50.0, 0, NOW + 60)
    for case in (no_reset, at_window_start, reset_beyond_window, zero_length):
        assert projected_pct(case, NOW, 0.0) is None
        assert window_level(case, NOW, CONFIG) == "normal"


def test_plan_level_is_the_worst_window():
    assert plan_level(quota("claude", five=10, week=95), NOW, CONFIG) == "critical"
    assert plan_level(quota("claude", five=75, week=10), NOW, CONFIG) == "conserve"
    assert plan_level(quota("claude", five=10, week=10), NOW, CONFIG) == "normal"


def test_unknown_quota_is_normal():
    assert plan_level(unknown("codex"), NOW, CONFIG) == "normal"


def test_weekly_projection_uses_the_weekly_window_only():
    q = quota("claude", five=90, week=45, five_elapsed=0.5, week_elapsed=0.5)
    assert weekly_projection(q, NOW, CONFIG) == pytest.approx(90.0)
    assert weekly_projection(quota("claude", week=45), NOW, CONFIG) is None
    assert weekly_projection(unknown("claude"), NOW, CONFIG) is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_policy_levels.py -q`
Expected: `ModuleNotFoundError: No module named 'model_router.policy'`

- [ ] **Step 4: Implement**

`model_router/policy.py`:

```python
"""The routing policy. Pure: no I/O, and the current time is passed in."""

from typing import Optional

from model_router.config import Config
from model_router.quota.types import (
    WEEK_MINUTES,
    PlanQuota,
    Window,
    effective_used_pct,
)

LEVEL_ORDER = ("normal", "conserve", "critical", "exhausted")
ALLOWED_WEIGHTS = {
    "normal": ("heavy", "medium", "light"),
    "conserve": ("medium", "light"),
    "critical": ("light",),
    "exhausted": (),
}


def projected_pct(window: Window, now: int, warmup: float) -> Optional[float]:
    """Usage at the end of the window if the current pace holds.

    Undefined when the window has no usable reset time, has not started, or
    is still inside the warmup, where early readings are too noisy.
    """
    if window.resets_at is None or window.resets_at <= now:
        return None
    if window.window_minutes <= 0:
        return None
    seconds = window.window_minutes * 60.0
    elapsed = max(0.0, min(1.0, 1.0 - (window.resets_at - now) / seconds))
    if elapsed <= 0.0 or elapsed < warmup:
        return None
    return window.used_pct / elapsed


def window_level(window: Window, now: int, config: Config) -> str:
    used = effective_used_pct(window, now)
    if used >= config.exhausted:
        return "exhausted"
    if used >= config.critical:
        return "critical"
    if used >= config.conserve:
        return "conserve"
    projected = projected_pct(window, now, config.warmup)
    if projected is not None and projected >= 100.0:
        return "conserve"
    return "normal"


def plan_level(quota: PlanQuota, now: int, config: Config) -> str:
    level = "normal"
    for window in quota.windows:
        candidate = window_level(window, now, config)
        if LEVEL_ORDER.index(candidate) > LEVEL_ORDER.index(level):
            level = candidate
    return level


def weekly_projection(quota: PlanQuota, now: int, config: Config) -> Optional[float]:
    for window in quota.windows:
        if window.window_minutes == WEEK_MINUTES:
            return projected_pct(window, now, config.warmup)
    return None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_policy_levels.py -q`
Expected: `16 passed`

- [ ] **Step 6: Commit**

```bash
git add model_router/policy.py tests/helpers.py tests/test_policy_levels.py
git commit -m "Add quota levels and projection"
```

---

### Task 7: Policy, part 2: redirect eligibility and `decide()`

**Files:**
- Modify: `model_router/policy.py` (replace the whole file with the version below; it keeps everything from Task 6)
- Test: `tests/test_policy_decide.py`

**Interfaces:**
- Consumes: `classify.Classification`, `config.Config`, `config.Target`, `config.PLANS`, `quota.types`.
- Produces, added to `policy.py`:
  - `ALWAYS_PASSTHROUGH`: agent types never altered whatever the config says
  - `Launch(agent_type: str, permission_mode: Optional[str], nested: bool, file_deny_rules: Optional[bool])`
  - `Decision(action: str, target: Optional[str], reason: str, levels: Dict[str, str], worker: Optional[str])`. `action` is `keep`, `set_model`, `redirect_codex`, or `deny`. `worker` is `"codex-read"` or `"codex-write"` for a redirect, otherwise `None`.
  - `needs_write(category: str, agent_type: str, config: Config) -> bool`
  - `redirect_block_reason(launch: Launch, write: bool, config: Config) -> Optional[str]` (`None` means eligible)
  - `decide(classification, launch, quotas: Dict[str, PlanQuota], config, now: int) -> Decision`

Order of rules inside `decide()` (§4): passthrough, then `[route:keep]`, then an explicit override, then the preference list with the balance step, then the lightest allowed target, then deny. A Codex target that is not eligible is skipped, never downgraded to the read worker.

- [ ] **Step 1: Write the failing tests**

`tests/test_policy_decide.py`:

```python
import pytest

from model_router.classify import Classification
from model_router.policy import Launch, decide, needs_write, redirect_block_reason
from model_router.quota.types import unknown
from tests.helpers import NOW, default_config, quota

CONFIG = default_config()


def launch(agent_type="general-purpose", mode="acceptEdits", nested=False, deny=False):
    return Launch(agent_type, mode, nested, deny)


def run(category, claude=None, codex=None, override=None, **launch_args):
    quotas = {
        "claude": claude or quota("claude"),
        "codex": codex or quota("codex"),
    }
    return decide(
        Classification(category, "keyword", override),
        launch(**launch_args),
        quotas,
        CONFIG,
        NOW,
    )


def test_normal_quota_takes_the_first_preference():
    decision = run("plan")
    assert (decision.action, decision.target, decision.worker) == ("set_model", "opus", None)
    assert decision.levels == {"claude": "normal", "codex": "normal"}
    assert decision.reason == "plan -> opus"


def test_a_codex_first_preference_redirects_with_the_write_worker():
    decision = run("implement")
    assert (decision.action, decision.target, decision.worker) == (
        "redirect_codex", "codex-deep", "codex-write",
    )


def test_conserve_skips_heavy_targets_on_that_plan():
    decision = run("plan", claude=quota("claude", five=75))
    assert (decision.action, decision.target, decision.worker) == (
        "redirect_codex", "codex-deep", "codex-read",
    )
    assert "opus skipped: claude conserve" in decision.reason


def test_falls_back_to_the_lightest_allowed_target():
    decision = run("plan", claude=quota("claude", five=92), codex=quota("codex", five=99))
    assert (decision.action, decision.target) == ("set_model", "haiku")
    assert "fallback" in decision.reason


def test_denies_when_nothing_is_allowed():
    decision = run("plan", claude=quota("claude", five=99), codex=quota("codex", five=99))
    assert decision.action == "deny" and decision.target is None
    assert "earliest reset" in decision.reason


@pytest.mark.parametrize(
    "agent", ["codex-review", "fork", "model-router:codex-read", "codex-write"]
)
def test_passthrough_agents_are_kept(agent):
    assert run("implement", agent_type=agent).action == "keep"


def test_keep_tag():
    assert run("implement", override="keep").action == "keep"


def test_override_beats_conserve_and_critical():
    decision = run("explore", claude=quota("claude", five=92), override="opus")
    assert (decision.action, decision.target) == ("set_model", "opus")
    assert "override" in decision.reason


def test_override_still_obeys_exhausted():
    decision = run("explore", claude=quota("claude", five=99), override="opus")
    assert decision.target == "codex"
    assert "override opus rejected: claude exhausted" in decision.reason


def test_override_to_codex_obeys_redirect_eligibility():
    decision = run("implement", override="codex-deep", mode="default")
    assert (decision.action, decision.target) == ("set_model", "sonnet")
    assert "override codex-deep rejected" in decision.reason


def test_unknown_tag_is_ignored_with_a_note():
    decision = run("plan", override="gpt9")
    assert decision.target == "opus"
    assert "unknown route tag 'gpt9' ignored" in decision.reason


@pytest.mark.parametrize("mode,redirects", [
    ("plan", False), ("default", False), ("auto", False), ("dontAsk", False),
    (None, False), ("weird", False),
    ("acceptEdits", True), ("bypassPermissions", True),
])
def test_write_redirect_by_permission_mode(mode, redirects):
    decision = run("implement", mode=mode)
    if redirects:
        assert (decision.action, decision.worker) == ("redirect_codex", "codex-write")
    else:
        assert (decision.action, decision.target, decision.worker) == (
            "set_model", "sonnet", None,
        )


@pytest.mark.parametrize("mode,redirects", [
    ("plan", False), ("dontAsk", False), (None, False), ("weird", False),
    ("default", True), ("auto", True),
    ("acceptEdits", True), ("bypassPermissions", True),
])
def test_read_redirect_by_permission_mode(mode, redirects):
    decision = run("review", mode=mode)
    if redirects:
        assert (decision.action, decision.target, decision.worker) == (
            "redirect_codex", "codex-deep", "codex-read",
        )
    else:
        assert (decision.action, decision.target, decision.worker) == (
            "set_model", "opus", None,
        )


@pytest.mark.parametrize("launch_args,fragment", [
    ({"nested": True}, "nested launch"),
    ({"agent_type": "my-custom-agent"}, "not redirectable"),
    ({"deny": True}, "deny rules"),
    ({"deny": None}, "settings could not be read"),
])
def test_blocked_redirects_stay_on_claude(launch_args, fragment):
    decision = run("implement", **launch_args)
    assert (decision.action, decision.target, decision.worker) == (
        "set_model", "sonnet", None,
    )
    assert fragment in decision.reason


def test_read_only_agents_never_get_the_write_worker():
    assert needs_write("implement", "general-purpose", CONFIG) is True
    assert needs_write("implement", "Explore", CONFIG) is False
    assert needs_write("review", "general-purpose", CONFIG) is False
    decision = run("implement", agent_type="Explore", mode="default")
    assert (decision.action, decision.worker) == ("redirect_codex", "codex-read")


def test_block_reason_is_none_when_eligible():
    assert redirect_block_reason(launch(), True, CONFIG) is None
    assert redirect_block_reason(launch(mode="default"), False, CONFIG) is None


def test_unknown_quota_is_treated_as_normal_and_noted():
    decision = run("plan", claude=unknown("claude"))
    assert decision.target == "opus"
    assert "claude quota unknown, treated as normal" in decision.reason


def balanced(claude_week, codex_week, **kwargs):
    return run(
        "plan",
        claude=quota("claude", week=claude_week, week_elapsed=0.5),
        codex=quota("codex", week=codex_week, week_elapsed=0.5),
        **kwargs
    )


def test_balance_promotes_the_underused_plan():
    decision = balanced(45, 10)  # weekly projections: 90% and 20%
    assert (decision.action, decision.target) == ("redirect_codex", "codex-deep")
    assert "balance" in decision.reason


@pytest.mark.parametrize("claude_week,codex_week", [(42, 10), (45, 30), (45, 35)])
def test_balance_needs_a_real_imbalance(claude_week, codex_week):
    assert balanced(claude_week, codex_week).target == "opus"


def test_balance_needs_both_projections():
    decision = run(
        "plan",
        claude=quota("claude", week=45, week_elapsed=0.5),
        codex=quota("codex", week=1),
    )
    assert decision.target == "opus"


def test_balance_never_picks_a_blocked_candidate():
    assert balanced(45, 10, mode="plan").target == "opus"


def test_override_skips_balance():
    assert balanced(45, 10, override="opus").target == "opus"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_policy_decide.py -q`
Expected: `ImportError: cannot import name 'Launch' from 'model_router.policy'`

- [ ] **Step 3: Implement**

Replace `model_router/policy.py` with:

```python
"""The routing policy. Pure: no I/O, and the current time is passed in."""

from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from model_router.classify import Classification
from model_router.config import PLANS, WEIGHTS, Config, Target
from model_router.quota.types import (
    WEEK_MINUTES,
    PlanQuota,
    Window,
    effective_used_pct,
    format_time,
    unknown,
)

LEVEL_ORDER = ("normal", "conserve", "critical", "exhausted")
ALLOWED_WEIGHTS = {
    "normal": ("heavy", "medium", "light"),
    "conserve": ("medium", "light"),
    "critical": ("light",),
    "exhausted": (),
}
# Never altered, whatever the config says. Covers the router's own workers
# (with and without the plugin namespace), which prevents redirect loops, and
# forks, which always inherit the parent's model.
ALWAYS_PASSTHROUGH = (
    "fork",
    "codex-read",
    "codex-write",
    "model-router:codex-read",
    "model-router:codex-write",
)


class Launch(NamedTuple):
    agent_type: str
    permission_mode: Optional[str]
    nested: bool
    file_deny_rules: Optional[bool]


class Decision(NamedTuple):
    action: str
    target: Optional[str]
    reason: str
    levels: Dict[str, str]
    worker: Optional[str]


def projected_pct(window: Window, now: int, warmup: float) -> Optional[float]:
    """Usage at the end of the window if the current pace holds.

    Undefined when the window has no usable reset time, has not started, or
    is still inside the warmup, where early readings are too noisy.
    """
    if window.resets_at is None or window.resets_at <= now:
        return None
    if window.window_minutes <= 0:
        return None
    seconds = window.window_minutes * 60.0
    elapsed = max(0.0, min(1.0, 1.0 - (window.resets_at - now) / seconds))
    if elapsed <= 0.0 or elapsed < warmup:
        return None
    return window.used_pct / elapsed


def window_level(window: Window, now: int, config: Config) -> str:
    used = effective_used_pct(window, now)
    if used >= config.exhausted:
        return "exhausted"
    if used >= config.critical:
        return "critical"
    if used >= config.conserve:
        return "conserve"
    projected = projected_pct(window, now, config.warmup)
    if projected is not None and projected >= 100.0:
        return "conserve"
    return "normal"


def plan_level(quota: PlanQuota, now: int, config: Config) -> str:
    level = "normal"
    for window in quota.windows:
        candidate = window_level(window, now, config)
        if LEVEL_ORDER.index(candidate) > LEVEL_ORDER.index(level):
            level = candidate
    return level


def weekly_projection(quota: PlanQuota, now: int, config: Config) -> Optional[float]:
    for window in quota.windows:
        if window.window_minutes == WEEK_MINUTES:
            return projected_pct(window, now, config.warmup)
    return None


def needs_write(category: str, agent_type: str, config: Config) -> bool:
    """Whether a redirect of this launch would need the write-enabled worker."""
    return (
        category in config.write_categories
        and agent_type not in config.readonly_agents
    )


def redirect_block_reason(launch: Launch, write: bool, config: Config) -> Optional[str]:
    """Why this launch may not be sent to Codex, or None if it may.

    A redirect leaves Claude Code's permission system for Codex's sandbox, so
    it is allowed only when everything the hook can see says the sandbox's
    file access is no wider than the session's. Anything unknown blocks.
    """
    if launch.agent_type not in config.redirectable:
        return "agent type %s is not redirectable" % launch.agent_type
    if launch.nested:
        return "nested launch"
    kind = "write" if write else "read"
    allowed = config.write_redirect_modes if write else config.read_redirect_modes
    if launch.permission_mode not in allowed:
        return "permission mode %s does not allow a %s redirect" % (
            launch.permission_mode or "unknown", kind,
        )
    if launch.file_deny_rules is None:
        return "settings could not be read"
    if launch.file_deny_rules:
        return "file-scoped deny rules are present"
    return None


def _balance(
    later_names: Sequence[str],
    chosen: Target,
    rejection: Callable[[Target], Optional[str]],
    quotas: Dict[str, PlanQuota],
    config: Config,
    now: int,
) -> Optional[Tuple[Target, str]]:
    """Promote a later list entry when the chosen plan is heading over."""
    own = weekly_projection(quotas[chosen.plan], now, config)
    if own is None or own <= config.overused:
        return None
    for name in later_names:
        candidate = config.targets[name]
        if candidate.plan == chosen.plan or rejection(candidate) is not None:
            continue
        other = weekly_projection(quotas[candidate.plan], now, config)
        if other is not None and other < config.underused:
            note = "balance: %s weekly projected %.0f%%, %s %.0f%%; promoted over %s" % (
                chosen.plan, own, candidate.plan, other, chosen.name,
            )
            return candidate, note
    return None


def _earliest_reset(
    quotas: Dict[str, PlanQuota], levels: Dict[str, str], now: int
) -> Optional[int]:
    resets = [
        window.resets_at
        for plan in PLANS
        if levels[plan] != "normal"
        for window in quotas[plan].windows
        if window.resets_at is not None and window.resets_at > now
    ]
    return min(resets) if resets else None


def decide(
    classification: Classification,
    launch: Launch,
    quotas: Dict[str, PlanQuota],
    config: Config,
    now: int,
) -> Decision:
    quotas = {plan: quotas.get(plan) or unknown(plan) for plan in PLANS}
    levels = {plan: plan_level(quotas[plan], now, config) for plan in PLANS}
    category = classification.category

    if launch.agent_type in ALWAYS_PASSTHROUGH or launch.agent_type in config.passthrough:
        return Decision(
            "keep", None, "%s is a passthrough agent type" % launch.agent_type,
            levels, None,
        )
    if classification.override == "keep":
        return Decision("keep", None, "kept by [route:keep]", levels, None)

    write = needs_write(category, launch.agent_type, config)
    block = redirect_block_reason(launch, write, config)
    notes: List[str] = [
        "%s quota unknown, treated as normal" % plan
        for plan in PLANS
        if not quotas[plan].known
    ]

    def rejection(target: Target, soft_levels: bool = True) -> Optional[str]:
        level = levels[target.plan]
        if soft_levels:
            if target.weight not in ALLOWED_WEIGHTS[level]:
                return "%s %s" % (target.plan, level)
        elif level == "exhausted":
            return "%s exhausted" % target.plan
        if target.plan == "codex" and block is not None:
            return block
        return None

    chosen: Optional[Target] = None
    override = classification.override
    if override is not None:
        target = config.targets.get(override)
        if target is None:
            notes.append("unknown route tag %r ignored" % override)
        else:
            # An explicit override ignores conserve and critical, but not
            # exhausted and not redirect eligibility.
            why = rejection(target, soft_levels=False)
            if why is None:
                chosen = target
                notes.append("override")
            else:
                notes.append("override %s rejected: %s" % (target.name, why))

    if chosen is None:
        names = config.preferences[category]
        for index, name in enumerate(names):
            target = config.targets[name]
            why = rejection(target)
            if why is not None:
                notes.append("%s skipped: %s" % (name, why))
                continue
            chosen = target
            promoted = _balance(names[index + 1:], target, rejection, quotas, config, now)
            if promoted is not None:
                chosen = promoted[0]
                notes.append(promoted[1])
            break

    if chosen is None:
        allowed = [t for t in config.targets.values() if rejection(t) is None]
        if allowed:
            # min() keeps the first of equals, so ties follow config order.
            chosen = min(allowed, key=lambda t: WEIGHTS.index(t.weight))
            notes.append("fallback to the lightest allowed target")

    if chosen is None:
        reason = "%s -> denied: no target is allowed (%s)" % (category, "; ".join(notes))
        reset = _earliest_reset(quotas, levels, now)
        if reset is not None:
            reason += "; earliest reset %s" % format_time(reset)
        return Decision("deny", None, reason, levels, None)

    reason = "%s -> %s" % (category, chosen.name)
    if notes:
        reason += " (%s)" % "; ".join(notes)
    if chosen.plan == "codex":
        worker = "codex-write" if write else "codex-read"
        return Decision("redirect_codex", chosen.name, reason, levels, worker)
    return Decision("set_model", chosen.name, reason, levels, None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_policy_decide.py tests/test_policy_levels.py -q`
Expected: `60 passed`

- [ ] **Step 5: Commit**

```bash
git add model_router/policy.py tests/test_policy_decide.py
git commit -m "Add redirect eligibility and the decide function"
```

---

### Task 8: Decision log and session state

**Files:**
- Create: `model_router/decision_log.py`, `model_router/state.py`
- Test: `tests/test_decision_log.py`, `tests/test_state.py`

**Interfaces:**
- Consumes: `fsutil.atomic_write_text`.
- Produces:
  - `decision_log.append_record(path: Path, record: Dict[str, Any], max_bytes: int = 5 * 1024 * 1024) -> None`
  - `decision_log.read_recent(path: Path, count: int) -> List[Dict[str, Any]]` (oldest first; bad lines skipped)
  - `state.load_state(path: Path) -> Dict[str, Any]` (always `{"sessions": {...}}`)
  - `state.session_entry(state, session_id: str, now: int) -> Dict[str, Any]` (creates the entry and stamps `seen_at`)
  - `state.save_state(path: Path, state, now: int) -> None` (drops sessions not seen for 7 days)

- [ ] **Step 1: Write the failing tests**

`tests/test_decision_log.py`:

```python
import stat

from model_router.decision_log import append_record, read_recent


def test_append_and_read_recent(tmp_path):
    log = tmp_path / "state" / "decisions.jsonl"
    for index in range(5):
        append_record(log, {"n": index})
    assert [record["n"] for record in read_recent(log, 3)] == [2, 3, 4]
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_rotates_at_the_size_limit(tmp_path):
    log = tmp_path / "decisions.jsonl"
    append_record(log, {"n": 1}, max_bytes=5)
    append_record(log, {"n": 2}, max_bytes=5)
    assert (tmp_path / "decisions.jsonl.1").exists()
    assert [record["n"] for record in read_recent(log, 10)] == [2]


def test_read_recent_skips_bad_lines_and_missing_files(tmp_path):
    log = tmp_path / "decisions.jsonl"
    assert read_recent(log, 5) == []
    log.write_text('{"n": 1}\nnot json\n[1]\n{"n": 2}\n')
    assert [record["n"] for record in read_recent(log, 10)] == [1, 2]
    assert read_recent(log, 0) == []
```

`tests/test_state.py`:

```python
from model_router.state import load_state, save_state, session_entry
from tests.helpers import NOW


def test_missing_or_malformed_state_is_empty(tmp_path):
    path = tmp_path / "state.json"
    assert load_state(path) == {"sessions": {}}
    for text in ("{not json", "[]", '{"sessions": []}'):
        path.write_text(text)
        assert load_state(path) == {"sessions": {}}


def test_session_entry_round_trip(tmp_path):
    path = tmp_path / "nested" / "state.json"
    state = load_state(path)
    session_entry(state, "s1", NOW)["announced"] = {"claude": "conserve"}
    save_state(path, state, NOW)
    again = load_state(path)
    assert session_entry(again, "s1", NOW)["announced"] == {"claude": "conserve"}


def test_old_sessions_are_pruned(tmp_path):
    path = tmp_path / "state.json"
    state = load_state(path)
    session_entry(state, "old", NOW - 8 * 24 * 3600)
    session_entry(state, "new", NOW)
    save_state(path, state, NOW)
    assert list(load_state(path)["sessions"]) == ["new"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_decision_log.py tests/test_state.py -q`
Expected: `ModuleNotFoundError` for `model_router.decision_log` and `model_router.state`

- [ ] **Step 3: Implement**

`model_router/decision_log.py`:

```python
"""Append-only log of routing decisions, one JSON object per line.

Records never contain prompt text or the launch description.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_MAX_BYTES = 5 * 1024 * 1024


def append_record(
    path: Path, record: Dict[str, Any], max_bytes: int = DEFAULT_MAX_BYTES
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if destination.stat().st_size >= max_bytes:
            os.replace(str(destination), str(destination) + ".1")
    except FileNotFoundError:
        pass
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(
        str(destination), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600
    )
    try:
        # One write per record keeps concurrent sessions from interleaving.
        os.write(descriptor, line.encode("utf-8"))
    finally:
        os.close(descriptor)


def read_recent(path: Path, count: int) -> List[Dict[str, Any]]:
    if count <= 0:
        return []
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    records: List[Dict[str, Any]] = []
    for line in lines[-count:]:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records
```

`model_router/state.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_decision_log.py tests/test_state.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add model_router/decision_log.py model_router/state.py tests/test_decision_log.py tests/test_state.py
git commit -m "Add the decision log and session state"
```

---

### Task 9: Permissions helper

**Files:**
- Create: `model_router/hooks/permissions.py`
- Test: `tests/test_permissions.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `settings_files(cwd: str, claude_home: Path, managed_settings: Path) -> List[Path]`
  - `has_file_deny_rules(files: Sequence[Path]) -> Optional[bool]`

Claude Code reads permission rules from the user settings (`~/.claude/settings.json`, `settings.local.json`), the project settings (`.claude/settings.json` and `.claude/settings.local.json` at the project root), and a managed settings file. A deny rule is a string such as `Read(./.env)`, `Edit(secrets/**)`, or a bare tool name. The hook only knows the session's `cwd`, which can be a subdirectory of the project, so every ancestor directory is checked.

`has_file_deny_rules` returns `True` if any file denies `Read`, `Edit`, or `Write`; `False` if none does; and `None` if any file exists but cannot be understood. `None` blocks redirects (§4.4).

- [ ] **Step 1: Write the failing tests**

`tests/test_permissions.py`:

```python
import json

import pytest

from model_router.hooks.permissions import has_file_deny_rules, settings_files


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def test_settings_files_cover_user_project_and_managed(tmp_path):
    home = tmp_path / "home" / ".claude"
    managed = tmp_path / "managed.json"
    files = settings_files(str(tmp_path / "work" / "repo" / "sub"), home, managed)
    assert home / "settings.json" in files
    assert home / "settings.local.json" in files
    assert managed in files
    assert tmp_path / "work" / "repo" / ".claude" / "settings.json" in files
    assert tmp_path / "work" / "repo" / "sub" / ".claude" / "settings.local.json" in files
    assert len(files) == len(set(files))


def test_empty_cwd_checks_only_user_and_managed(tmp_path):
    files = settings_files("", tmp_path / ".claude", tmp_path / "managed.json")
    assert len(files) == 3


def test_no_settings_means_no_deny_rules(tmp_path):
    assert has_file_deny_rules([tmp_path / "absent.json"]) is False


def test_a_bash_only_deny_rule_does_not_count(tmp_path):
    path = write(tmp_path / "s.json", {"permissions": {"deny": ["Bash(rm -rf:*)"]}})
    assert has_file_deny_rules([path]) is False


@pytest.mark.parametrize("rule", ["Read(./.env)", "Edit(secrets/**)", "Write", "Read"])
def test_a_file_scoped_deny_rule_counts(tmp_path, rule):
    path = write(tmp_path / "s.json", {"permissions": {"deny": ["Bash(ls)", rule]}})
    assert has_file_deny_rules([tmp_path / "absent.json", path]) is True


@pytest.mark.parametrize("text", [
    "{not json", "[]", '{"permissions": []}', '{"permissions": {"deny": "Read"}}',
])
def test_settings_that_cannot_be_understood_return_none(tmp_path, text):
    assert has_file_deny_rules([write(tmp_path / "s.json", text)]) is None


def test_one_unreadable_file_decides_the_result(tmp_path):
    good = write(tmp_path / "good.json", {"permissions": {"deny": []}})
    bad = write(tmp_path / "bad.json", "{not json")
    assert has_file_deny_rules([good, bad]) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_permissions.py -q`
Expected: `ModuleNotFoundError: No module named 'model_router.hooks.permissions'`

- [ ] **Step 3: Implement**

`model_router/hooks/permissions.py`:

```python
"""Detect file-scoped deny rules in the Claude Code settings.

Codex cannot honour Claude Code's per-path permission rules. If any exist,
no launch may be redirected to Codex. This module only answers "do any
exist?"; it never interprets the paths in a rule.
"""

import json
from pathlib import Path
from typing import Any, List, Optional, Sequence

FILE_TOOLS = ("Read", "Edit", "Write")
SETTINGS_NAMES = ("settings.json", "settings.local.json")


def settings_files(cwd: str, claude_home: Path, managed_settings: Path) -> List[Path]:
    files: List[Path] = [Path(claude_home) / name for name in SETTINGS_NAMES]
    files.append(Path(managed_settings))
    if cwd:
        directory = Path(cwd)
        for folder in [directory] + list(directory.parents):
            for name in SETTINGS_NAMES:
                candidate = folder / ".claude" / name
                if candidate not in files:
                    files.append(candidate)
    return files


def _is_file_rule(rule: Any) -> bool:
    if not isinstance(rule, str):
        return False
    return rule.split("(", 1)[0].strip() in FILE_TOOLS


def has_file_deny_rules(files: Sequence[Path]) -> Optional[bool]:
    found = False
    for path in files:
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        permissions = data.get("permissions", {})
        if not isinstance(permissions, dict):
            return None
        deny = permissions.get("deny", [])
        if not isinstance(deny, list):
            return None
        if any(_is_file_rule(rule) for rule in deny):
            found = True
    return found
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_permissions.py -q`
Expected: `13 passed`

- [ ] **Step 5: Commit**

```bash
git add model_router/hooks/permissions.py tests/test_permissions.py
git commit -m "Add file-scoped deny rule detection"
```

---

### Task 10: CLI (`status`, `explain`, `log`), entry script, example config

This task completes spec phases 1 and 2: the owner can see both plans' quota and dry-run decisions with no hooks installed.

**Files:**
- Create: `model_router/cli.py`, `bin/router`, `config.example.jsonc`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `paths.from_env`, `config.load_config`, `config.build_config`, `config.PLANS`, `quota.read_quotas`, `quota.types` helpers, `classify.classify`, `policy` (`Launch`, `decide`, `plan_level`, `projected_pct`), `hooks.permissions`, `decision_log.read_recent`.
- Produces:
  - `cli.render_status(paths: Paths, now: int) -> str`
  - `cli.render_explain(task: str, agent_type: str, permission_mode: str, cwd: str, paths: Paths, now: int) -> str`
  - `cli.render_log(paths: Paths, count: int) -> str`
  - `cli.main(argv: List[str]) -> int`
  - `bin/router`: executable entry script. Later tasks add the `hook`, `statusline`, and `codex-run` subcommands to `cli.main`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
import subprocess
from pathlib import Path

from model_router.cli import main, render_explain, render_log, render_status
from model_router.config import load_config
from model_router.decision_log import append_record
from tests.helpers import NOW, default_config, seed_claude

ROOT = Path(__file__).resolve().parents[1]


def test_status_with_no_config_file(paths):
    seed_claude(paths, five=75, week=10)
    text = render_status(paths, NOW)
    assert "Config: absent" in text
    assert "Claude: conserve" in text
    assert "5h" in text and "75%" in text
    assert "Codex: quota unknown" in text


def test_status_lists_validation_errors(paths):
    paths.config.write_text('{"mdoe": "x"}')
    text = render_status(paths, NOW)
    assert "INVALID" in text and "mdoe: unknown key" in text


def test_status_shows_the_mode_of_a_valid_config(paths):
    paths.config.write_text('{"mode": "enforce"}')
    assert "Config: valid, mode enforce" in render_status(paths, NOW)


def test_explain_shows_the_decision(paths):
    text = render_explain(
        "Implement the cache", "general-purpose", "acceptEdits", "", paths, NOW
    )
    assert "Category: implement (from keyword)" in text
    assert "Decision: redirect_codex -> codex-deep [codex-write]" in text
    assert "launch unchanged" in text


def test_explain_with_an_invalid_config(paths):
    paths.config.write_text("{not json")
    text = render_explain("Implement it", "general-purpose", "default", "", paths, NOW)
    assert "kept unchanged" in text and "not valid JSON" in text


def test_log_renders_decisions_and_events(paths):
    append_record(paths.log, {
        "ts": NOW, "action": "set_model", "target": "opus", "reason": "plan -> opus",
    })
    append_record(paths.log, {"ts": NOW, "event": "hook_error", "error": "boom"})
    text = render_log(paths, 10)
    assert "set_model" in text and "plan -> opus" in text
    assert "hook_error" in text and "boom" in text


def test_log_when_empty(paths):
    assert render_log(paths, 10) == "No decisions logged yet."


def test_main_reads_locations_from_the_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MODEL_ROUTER_CONFIG", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_STATE_DIR", raising=False)
    assert main(["status"]) == 0
    assert "Config: absent" in capsys.readouterr().out


def test_entry_script_runs_under_isolated_python(tmp_path):
    result = subprocess.run(
        ["/usr/bin/python3", "-I", str(ROOT / "bin" / "router"), "status"],
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Config: absent" in result.stdout


def test_example_config_is_valid_and_matches_the_defaults():
    result = load_config(ROOT / "config.example.jsonc")
    assert result.status == "valid"
    assert result.config == default_config()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: `ModuleNotFoundError: No module named 'model_router.cli'`

- [ ] **Step 3: Implement the CLI**

`model_router/cli.py`:

```python
"""Command-line entry point: status, explain, and log."""

import argparse
import os
import time
from typing import List

from model_router.classify import classify
from model_router.config import PERMISSION_MODES, PLANS, build_config, load_config
from model_router.decision_log import read_recent
from model_router.hooks.permissions import has_file_deny_rules, settings_files
from model_router.paths import Paths, from_env
from model_router.policy import Launch, decide, plan_level, projected_pct
from model_router.quota import read_quotas
from model_router.quota.types import effective_used_pct, format_time, window_label


def _window_line(window, now: int, config) -> str:
    projected = projected_pct(window, now, config.warmup)
    shown = "%3.0f%%" % projected if projected is not None else "   -"
    resets = (
        format_time(window.resets_at)
        if window.resets_at is not None and window.resets_at > now
        else "-"
    )
    return "  %-7s %3.0f%%  projected %s  resets %s" % (
        window_label(window), effective_used_pct(window, now), shown, resets,
    )


def render_status(paths: Paths, now: int) -> str:
    result = load_config(paths.config)
    lines: List[str] = []
    if result.status == "valid":
        lines.append("Config: valid, mode %s (%s)" % (result.config.mode, paths.config))
    elif result.status == "absent":
        lines.append(
            "Config: absent, built-in defaults in shadow mode (%s)" % paths.config
        )
    else:
        lines.append(
            "Config: INVALID, routing is off and launches are unchanged (%s)"
            % paths.config
        )
        lines.extend("  - %s" % error for error in result.errors)
    # Levels are still worth showing when the config is broken.
    config = result.config or build_config({})[0]
    quotas = read_quotas(paths)
    for plan in PLANS:
        quota = quotas[plan]
        if not quota.known:
            lines.append("%s: quota unknown (treated as normal)" % plan.capitalize())
            continue
        lines.append("%s: %s" % (plan.capitalize(), plan_level(quota, now, config)))
        lines.extend(_window_line(window, now, config) for window in quota.windows)
    return "\n".join(lines)


def render_explain(
    task: str, agent_type: str, permission_mode: str, cwd: str, paths: Paths, now: int
) -> str:
    result = load_config(paths.config)
    if result.config is None:
        return "\n".join(
            ["Config is invalid, so every launch is kept unchanged."]
            + ["  - %s" % error for error in result.errors]
        )
    config = result.config
    tool_input = {"subagent_type": agent_type, "description": task, "prompt": task}
    classification = classify(tool_input, config)
    launch = Launch(
        agent_type,
        permission_mode,
        False,
        has_file_deny_rules(
            settings_files(cwd, paths.claude_home, paths.managed_settings)
        ),
    )
    decision = decide(classification, launch, read_quotas(paths), config, now)
    outcome = decision.action
    if decision.target:
        outcome += " -> %s" % decision.target
    if decision.worker:
        outcome += " [%s]" % decision.worker
    mode = config.mode
    if mode != "enforce":
        mode += " (the decision is logged at most; launch unchanged)"
    return "\n".join([
        "Category: %s (from %s)" % (classification.category, classification.source),
        "Levels: " + ", ".join("%s %s" % (p, decision.levels[p]) for p in PLANS),
        "Decision: %s" % outcome,
        "Reason: %s" % decision.reason,
        "Mode: %s" % mode,
    ])


def render_log(paths: Paths, count: int) -> str:
    records = read_recent(paths.log, count)
    if not records:
        return "No decisions logged yet."
    lines: List[str] = []
    for record in records:
        timestamp = record.get("ts")
        when = format_time(timestamp) if isinstance(timestamp, (int, float)) else "?"
        if "action" in record:
            lines.append("%s  %-14s %-11s %s" % (
                when, record["action"], record.get("target") or "-",
                record.get("reason", ""),
            ))
        else:
            detail = record.get("error") or "; ".join(record.get("errors", []))
            lines.append("%s  %-14s %s" % (when, record.get("event", "?"), detail))
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="router", description="Model router for Claude Code."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show quota, levels, and config state")
    explain = commands.add_parser("explain", help="dry-run a routing decision")
    explain.add_argument("task", help="the task text, as you would describe it")
    explain.add_argument("--agent", default="general-purpose")
    explain.add_argument(
        "--permission-mode", default="default", choices=PERMISSION_MODES
    )
    log = commands.add_parser("log", help="show recent decisions")
    log.add_argument("-n", type=int, default=20)
    return parser


def main(argv: List[str]) -> int:
    args = _parser().parse_args(argv)
    paths = from_env(os.environ)
    now = int(time.time())
    if args.command == "status":
        print(render_status(paths, now))
    elif args.command == "explain":
        print(render_explain(
            args.task, args.agent, args.permission_mode, os.getcwd(), paths, now
        ))
    elif args.command == "log":
        print(render_log(paths, args.n))
    return 0
```

- [ ] **Step 4: Create the entry script**

`bin/router`:

```python
#!/usr/bin/python3 -I
"""Entry point for the model router.

Runs in isolated mode, which does not put this script's directory on
sys.path, so the repository root is added here.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

try:
    from model_router.cli import main

    code = main(sys.argv[1:])
except Exception:
    # A hook or status line must never fail loudly or block a launch.
    if sys.argv[1:2] in (["hook"], ["statusline"]):
        code = 0
    else:
        raise
sys.exit(code)
```

Then make it executable:

```bash
chmod +x bin/router
```

- [ ] **Step 5: Create the example config**

`config.example.jsonc`:

```jsonc
// Model router configuration.
// Copy to ~/.config/model-router/config.jsonc and edit.
//
// The router changes launches only when this file exists, passes validation,
// and sets "mode" to "enforce". A file with any error makes the router inert.
// Run `router status` to check it.
{
  // "enforce": apply decisions. "shadow": log them, change nothing. "off".
  "mode": "shadow",

  // A target is a model you can route to. "weight" decides when it is paused:
  // heavy targets stop at conserve, medium at critical, light at exhausted.
  "targets": {
    "opus":       { "plan": "claude", "model": "opus",   "weight": "heavy"  },
    "sonnet":     { "plan": "claude", "model": "sonnet", "weight": "medium" },
    "haiku":      { "plan": "claude", "model": "haiku",  "weight": "light"  },
    "codex-deep": { "plan": "codex",  "effort": "xhigh",  "weight": "heavy"  },
    "codex":      { "plan": "codex",  "effort": "medium", "weight": "medium" }
  },

  // For each task category, your preferred targets in order.
  "preferences": {
    "plan":      ["opus", "codex-deep", "sonnet"],
    "implement": ["codex-deep", "sonnet", "codex"],
    "review":    ["codex-deep", "opus", "sonnet"],
    "explore":   ["haiku", "codex", "sonnet"],
    "debug":     ["opus", "codex-deep", "sonnet"],
    "default":   ["sonnet", "codex"]
  },

  // Percent used at which a plan changes level.
  "thresholds": { "conserve": 70, "critical": 90, "exhausted": 98 },
  // Ignore the projection until this fraction of a window has passed.
  "projection": { "warmup": 0.15 },
  // Weekly projections that trigger moving work to the other plan.
  "balance": { "overused": 85, "underused": 60 },

  "agent_categories": { "Explore": "explore", "explorer": "explore", "Plan": "plan" },
  "keywords": {
    "review":    ["review", "audit", "critique"],
    "debug":     ["debug", "root cause", "failing", "flaky", "stack trace"],
    "plan":      ["plan", "design", "architecture"],
    "explore":   ["find", "locate", "search", "where is", "trace how"],
    "implement": ["implement", "add", "build", "refactor", "fix", "write"]
  },

  // Agent types the router never touches.
  "passthrough": ["codex-review", "design-review", "implementer",
                  "statusline-setup", "claude-code-guide"],
  // Agent types that may be sent to Codex. Others get Claude model changes only.
  "redirectable": ["general-purpose", "claude", "Explore", "Plan", "explorer"],
  "readonly_agents": ["Explore", "Plan", "explorer"],
  "write_categories": ["implement", "debug", "default"],

  // Session permission modes in which a task may be sent to Codex.
  // Add "auto" to write_redirect_modes to allow unattended edits inside the
  // Codex workspace sandbox while your session is in auto mode.
  "read_redirect_modes":  ["default", "auto", "acceptEdits", "bypassPermissions"],
  "write_redirect_modes": ["acceptEdits", "bypassPermissions"],

  // A command to run after the status line captures quota, or null.
  "statusline": { "passthrough": null }
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: `10 passed`

- [ ] **Step 7: Check it against this machine's real data**

Run: `bin/router status`
Expected: a `Config: absent` line, then `Claude: quota unknown (treated as normal)`, because the router's own Claude snapshot is not captured until Task 13, then a level and two windows for Codex with real percentages from this machine's Codex logs.

Run: `bin/router explain "Review the payment retry logic" --permission-mode auto`
Expected: `Category: review (from keyword)` and a `Decision:` line.

- [ ] **Step 8: Commit**

```bash
git add model_router/cli.py bin/router config.example.jsonc tests/test_cli.py
git commit -m "Add the status, explain, and log commands"
```

---

### Task 11: Pre-launch hook adapter

**Files:**
- Modify: `model_router/hooks/__init__.py` (currently empty)
- Create: `model_router/hooks/pre_agent.py`
- Modify: `model_router/cli.py`
- Test: `tests/test_pre_agent.py`

**Interfaces:**
- Consumes: `config.load_config`, `classify.classify`, `policy` (`Launch`, `Decision`, `decide`), `quota.read_quotas`, `hooks.permissions`, `decision_log.append_record`, `state` functions, `paths.Paths`.
- Produces:
  - `hooks.run_hook(name: str, handle, stdin_text: str, paths: Paths, now: int, env: Mapping[str, str]) -> str`. Returns the text to print, `""` for nothing. Never raises.
  - `hooks.pre_agent.handle(payload, paths: Paths, now: int, env: Mapping[str, str]) -> Optional[Dict[str, Any]]`
  - `hooks.pre_agent.build_response(decision: Decision, tool_input: Dict[str, Any], config: Config) -> Optional[Dict[str, Any]]`
  - `router hook pre-agent` on the command line

Facts about the hook contract, verified against the Claude Code hooks reference:

- The event is `PreToolUse` and the subagent launch tool is named `Agent`. Its `tool_input` has `prompt`, `description`, `subagent_type`, and an optional `model`.
- The hook input also carries `session_id`, `cwd`, and `permission_mode`. `agent_id` is present only when the hook fires inside a subagent.
- To change the input, print `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "permissionDecisionReason": "...", "updatedInput": {...}}}`. `updatedInput` **replaces the whole input**, so every unchanged field must be included.
- To deny, use `"permissionDecision": "deny"` with a reason. The reason is shown to Claude.
- Printing nothing and exiting 0 leaves the launch unchanged.
- `"allow"` is how the hook contract carries `updatedInput`. It also means that one `Agent` call is not put to a permission prompt. Subagent launches are not normally prompted, and the tools the subagent then uses still go through the session's permission rules as usual. The live checks in Task 13 Step 8 and Task 16 Step 3 confirm this on the owner's setup.

A plugin agent named `codex-read` in the plugin `model-router` is addressed as `model-router:codex-read`.

- [ ] **Step 1: Write the failing tests**

`tests/test_pre_agent.py`:

```python
import json
import subprocess
from pathlib import Path

import pytest

from model_router.decision_log import read_recent
from model_router.hooks import run_hook
from model_router.hooks.pre_agent import handle
from tests.helpers import NOW, seed_claude, seed_codex

ROOT = Path(__file__).resolve().parents[1]


def payload(
    description="Implement the cache",
    prompt="Implement the cache layer",
    subagent_type="general-purpose",
    mode="acceptEdits",
    **extra
):
    data = {
        "session_id": "s1",
        "cwd": "/nonexistent-project",
        "permission_mode": mode,
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": subagent_type,
            "description": description,
            "prompt": prompt,
        },
    }
    data.update(extra)
    return data


def enforce(paths):
    paths.config.write_text('{"mode": "enforce"}')


def output(response):
    return response["hookSpecificOutput"]


def test_shadow_mode_logs_and_changes_nothing(paths):
    paths.config.write_text("{}")
    assert handle(payload(), paths, NOW, {}) is None
    records = read_recent(paths.log, 10)
    assert len(records) == 1
    assert records[0]["action"] == "redirect_codex" and records[0]["mode"] == "shadow"


def test_no_config_file_behaves_as_shadow(paths):
    assert handle(payload(), paths, NOW, {}) is None
    assert read_recent(paths.log, 10)[0]["mode"] == "shadow"


def test_enforce_sets_the_model_and_keeps_every_other_field(paths):
    enforce(paths)
    request = payload(description="Plan the migration", prompt="Plan the migration steps")
    result = output(handle(request, paths, NOW, {}))
    assert result["hookEventName"] == "PreToolUse"
    assert result["permissionDecision"] == "allow"
    assert result["permissionDecisionReason"].startswith("Model router: ")
    assert result["updatedInput"] == dict(request["tool_input"], model="opus")


def test_no_output_when_the_model_already_matches(paths):
    enforce(paths)
    request = payload(description="Plan the migration", prompt="Plan it")
    request["tool_input"]["model"] = "opus"
    assert handle(request, paths, NOW, {}) is None


def test_enforce_redirects_to_the_codex_worker(paths):
    enforce(paths)
    request = payload()
    request["tool_input"]["model"] = "opus"
    updated = output(handle(request, paths, NOW, {}))["updatedInput"]
    assert updated["subagent_type"] == "model-router:codex-write"
    assert "model" not in updated
    assert updated["prompt"] == "ROUTER_EFFORT: xhigh\nImplement the cache layer"
    assert updated["description"] == "Implement the cache"


def test_enforce_denies_when_everything_is_exhausted(paths):
    enforce(paths)
    seed_claude(paths, five=99, week=10)
    seed_codex(paths, five=99, week=10)
    result = output(handle(payload(), paths, NOW, {}))
    assert result["permissionDecision"] == "deny"
    assert "updatedInput" not in result
    assert result["permissionDecisionReason"].startswith("Model router: ")


@pytest.mark.parametrize("text", [
    "{not json",
    '{"mode": "enforce", "thresholds": {"conserve": 95}}',
    '{"mdoe": "shadow", "mode": "enforce"}',
])
def test_invalid_config_makes_the_router_inert(paths, text):
    paths.config.write_text(text)
    assert handle(payload(), paths, NOW, {}) is None
    assert handle(payload(), paths, NOW, {}) is None
    records = read_recent(paths.log, 10)
    assert [record.get("event") for record in records] == ["config_invalid"]


def test_environment_off_switch(paths):
    enforce(paths)
    assert handle(payload(), paths, NOW, {"MODEL_ROUTER": "off"}) is None
    assert not paths.log.exists()


def test_mode_off(paths):
    paths.config.write_text('{"mode": "off"}')
    assert handle(payload(), paths, NOW, {}) is None
    assert not paths.log.exists()


def test_other_tools_are_ignored(paths):
    enforce(paths)
    assert handle(payload(tool_name="Bash"), paths, NOW, {}) is None
    assert not paths.log.exists()


def test_non_string_launch_fields_do_not_crash(paths):
    enforce(paths)
    request = payload()
    request["tool_input"] = {"subagent_type": ["x"], "description": 5, "prompt": None}
    updated = output(handle(request, paths, NOW, {}))["updatedInput"]
    assert updated == {
        "subagent_type": ["x"], "description": 5, "prompt": None, "model": "sonnet",
    }


def test_the_record_has_audit_fields_and_no_prompt_text(paths):
    request = payload(
        description="SECRET-DESC implement the cache",
        prompt="SECRET-TOKEN-123 implement the cache",
    )
    handle(request, paths, NOW, {})
    record = read_recent(paths.log, 1)[0]
    assert record["category"] == "implement"
    assert record["classified_by"] == "keyword"
    assert record["agent_type"] == "general-purpose"
    assert record["permission_mode"] == "acceptEdits"
    assert record["nested"] is False and record["deny_rules"] is False
    assert record["session_id"] == "s1" and record["ts"] == NOW
    raw = paths.log.read_text()
    assert "SECRET-TOKEN-123" not in raw and "SECRET-DESC" not in raw


def test_a_nested_launch_is_not_redirected(paths):
    enforce(paths)
    result = output(handle(payload(agent_id="agent-1"), paths, NOW, {}))
    assert result["updatedInput"]["model"] == "sonnet"
    assert result["updatedInput"]["subagent_type"] == "general-purpose"
    assert read_recent(paths.log, 1)[0]["nested"] is True


def test_file_deny_rules_block_the_redirect(paths):
    enforce(paths)
    paths.claude_home.mkdir(parents=True)
    (paths.claude_home / "settings.json").write_text(
        '{"permissions": {"deny": ["Read(./.env)"]}}'
    )
    result = output(handle(payload(), paths, NOW, {}))
    assert result["updatedInput"]["model"] == "sonnet"
    assert read_recent(paths.log, 1)[0]["deny_rules"] is True


@pytest.mark.parametrize("stdin_text", ["", "not json", "[]", "null"])
def test_run_hook_prints_nothing_for_bad_input(paths, stdin_text):
    enforce(paths)
    assert run_hook("pre_agent", handle, stdin_text, paths, NOW, {}) == ""


def test_run_hook_returns_the_response_as_json(paths):
    enforce(paths)
    text = run_hook("pre_agent", handle, json.dumps(payload()), paths, NOW, {})
    assert json.loads(text)["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_an_unwritable_state_directory_leaves_the_launch_unchanged(paths, tmp_path):
    enforce(paths)
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    broken = paths._replace(state_dir=blocker / "state")
    assert run_hook("pre_agent", handle, json.dumps(payload()), broken, NOW, {}) == ""


def test_run_hook_logs_an_unexpected_error(paths):
    def boom(payload, paths, now, env):
        raise RuntimeError("boom")

    assert run_hook("pre_agent", boom, "{}", paths, NOW, {}) == ""
    record = read_recent(paths.log, 1)[0]
    assert record["event"] == "hook_error" and record["error"] == "RuntimeError: boom"


def run_entry(tmp_path, stdin_text):
    return subprocess.run(
        ["/usr/bin/python3", "-I", str(ROOT / "bin" / "router"), "hook", "pre-agent"],
        input=stdin_text,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )


def test_entry_script_exits_zero_on_garbage(tmp_path):
    result = run_entry(tmp_path, "not json at all")
    assert (result.returncode, result.stdout) == (0, "")


def test_entry_script_is_silent_in_shadow_mode(tmp_path):
    result = run_entry(tmp_path, json.dumps(payload()))
    assert (result.returncode, result.stdout) == (0, "")
    log = tmp_path / "Library/Caches/model-router/decisions.jsonl"
    assert json.loads(log.read_text().splitlines()[0])["mode"] == "shadow"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pre_agent.py -q`
Expected: `ImportError: cannot import name 'run_hook' from 'model_router.hooks'`

- [ ] **Step 3: Implement the fail-open wrapper**

`model_router/hooks/__init__.py`:

```python
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
```

- [ ] **Step 4: Implement the adapter**

`model_router/hooks/pre_agent.py`:

```python
"""PreToolUse adapter for the Agent tool: route a subagent launch."""

from typing import Any, Dict, List, Mapping, Optional

from model_router.classify import classify
from model_router.config import Config, load_config
from model_router.decision_log import append_record
from model_router.hooks.permissions import has_file_deny_rules, settings_files
from model_router.paths import Paths
from model_router.policy import Decision, Launch, decide
from model_router.quota import read_quotas
from model_router.state import load_state, save_state, session_entry

WORKER_PREFIX = "model-router:"
REASON_PREFIX = "Model router: "
DEFAULT_AGENT_TYPE = "general-purpose"


def _output(fields: Dict[str, Any]) -> Dict[str, Any]:
    fields = dict(fields, hookEventName="PreToolUse")
    return {"hookSpecificOutput": fields}


def build_response(
    decision: Decision, tool_input: Dict[str, Any], config: Config
) -> Optional[Dict[str, Any]]:
    if decision.action == "keep":
        return None
    reason = REASON_PREFIX + decision.reason
    if decision.action == "deny":
        return _output({
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        })
    target = config.targets[decision.target]
    # updatedInput replaces the whole input, so start from a full copy.
    updated = dict(tool_input)
    if decision.action == "set_model":
        if tool_input.get("model") == target.model:
            return None
        updated["model"] = target.model
    else:
        prompt = tool_input.get("prompt")
        updated["subagent_type"] = WORKER_PREFIX + decision.worker
        updated.pop("model", None)
        updated["prompt"] = "ROUTER_EFFORT: %s\n%s" % (
            target.effort, prompt if isinstance(prompt, str) else "",
        )
    return _output({
        "permissionDecision": "allow",
        "permissionDecisionReason": reason,
        "updatedInput": updated,
    })


def _log_invalid_once(
    paths: Paths, session_id: str, errors: List[str], now: int
) -> None:
    state = load_state(paths.state)
    entry = session_entry(state, session_id, now)
    if entry.get("config_invalid_logged"):
        return
    entry["config_invalid_logged"] = True
    save_state(paths.state, state, now)
    append_record(paths.log, {
        "ts": int(now),
        "event": "config_invalid",
        "session_id": session_id,
        "errors": errors,
    })


def handle(
    payload: Any, paths: Paths, now: int, env: Mapping[str, str]
) -> Optional[Dict[str, Any]]:
    if env.get("MODEL_ROUTER") == "off" or not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != "Agent":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    session_id = payload.get("session_id")
    session_id = session_id if isinstance(session_id, str) else ""

    result = load_config(paths.config)
    if result.config is None:
        # An invalid config must never turn enforcement on. Go inert.
        _log_invalid_once(paths, session_id, result.errors, now)
        return None
    config = result.config
    if config.mode == "off":
        return None

    agent_type = tool_input.get("subagent_type")
    if not isinstance(agent_type, str) or not agent_type:
        agent_type = DEFAULT_AGENT_TYPE
    permission_mode = payload.get("permission_mode")
    cwd = payload.get("cwd")
    launch = Launch(
        agent_type=agent_type,
        permission_mode=permission_mode if isinstance(permission_mode, str) else None,
        nested=bool(payload.get("agent_id")),
        file_deny_rules=has_file_deny_rules(settings_files(
            cwd if isinstance(cwd, str) else "",
            paths.claude_home,
            paths.managed_settings,
        )),
    )
    classification = classify(tool_input, config)
    decision = decide(classification, launch, read_quotas(paths), config, now)

    # No prompt text and no description: only what is needed to audit routing.
    append_record(paths.log, {
        "ts": int(now),
        "session_id": session_id,
        "agent_type": agent_type,
        "category": classification.category,
        "classified_by": classification.source,
        "action": decision.action,
        "target": decision.target,
        "worker": decision.worker,
        "reason": decision.reason,
        "levels": decision.levels,
        "mode": config.mode,
        "permission_mode": launch.permission_mode,
        "nested": launch.nested,
        "deny_rules": launch.file_deny_rules,
    })
    if config.mode != "enforce":
        return None
    return build_response(decision, tool_input, config)
```

- [ ] **Step 5: Wire the `hook` subcommand into the CLI**

In `model_router/cli.py`, add these imports below the existing ones:

```python
import sys

from model_router.hooks import run_hook
from model_router.hooks import pre_agent
```

Add this above `def _parser()`:

```python
_HOOK_HANDLERS = {
    "pre-agent": ("pre_agent", pre_agent.handle),
}


def _run_hook_command(argv: List[str]) -> int:
    """Run a hook. Always returns 0: exit code 2 would block the launch."""
    try:
        entry = _HOOK_HANDLERS.get(argv[0] if argv else "")
        if entry is None:
            return 0
        text = run_hook(
            entry[0], entry[1], sys.stdin.read(),
            from_env(os.environ), int(time.time()), os.environ,
        )
        if text:
            sys.stdout.write(text + "\n")
    except Exception:
        pass
    return 0
```

Change the first line of `main` so hooks never reach `argparse`, whose usage errors exit with code 2:

```python
def main(argv: List[str]) -> int:
    if argv[:1] == ["hook"]:
        return _run_hook_command(argv[1:])
    args = _parser().parse_args(argv)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pre_agent.py -q`
Expected: `25 passed`

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass, no warnings about unknown marks.

- [ ] **Step 8: Commit**

```bash
git add model_router/hooks/__init__.py model_router/hooks/pre_agent.py model_router/cli.py tests/test_pre_agent.py
git commit -m "Add the pre-launch hook adapter"
```

---

### Task 12: Status-line capture with passthrough

**Files:**
- Create: `model_router/hooks/statusline.py`
- Modify: `model_router/cli.py`
- Test: `tests/test_statusline.py`

**Interfaces:**
- Consumes: `quota.claude.write_snapshot`, `config.raw_statusline_passthrough`, `paths.Paths`.
- Produces:
  - `hooks.statusline.run(stdin_text: str, paths: Paths, now: int, runner=subprocess.run) -> str` (the text to print as the status line)
  - `router statusline` on the command line

Claude Code runs one status-line command, passes it a JSON object on stdin, and displays its stdout. The router must take over that slot to see quota, so it relays the same stdin to the command that was there before and prints that command's output. The passthrough command is read with `raw_statusline_passthrough`, which ignores validation, so a broken config does not blank the owner's status line.

- [ ] **Step 1: Write the failing tests**

`tests/test_statusline.py`:

```python
import json
import subprocess

from model_router.hooks.statusline import run
from model_router.quota.claude import read_claude_quota
from tests.helpers import NOW

STATUS_INPUT = json.dumps({
    "session_id": "s",
    "rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": NOW + 1000}},
})


class FakeRunner:
    def __init__(self, stdout="relayed", error=None):
        self.stdout = stdout
        self.error = error
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(command, 0, stdout=self.stdout, stderr="")


def with_passthrough(paths, command="my-statusline --flag"):
    paths.config.write_text(json.dumps({"statusline": {"passthrough": command}}))


def test_captures_quota_and_prints_nothing_without_a_passthrough(paths):
    runner = FakeRunner()
    assert run(STATUS_INPUT, paths, NOW, runner) == ""
    assert read_claude_quota(paths.claude_quota).windows[0].used_pct == 12.0
    assert runner.calls == []


def test_relays_stdin_to_the_passthrough_and_prints_its_output(paths):
    with_passthrough(paths)
    runner = FakeRunner(stdout="branch main\n")
    assert run(STATUS_INPUT, paths, NOW, runner) == "branch main\n"
    command, kwargs = runner.calls[0]
    assert command == "my-statusline --flag"
    assert kwargs["input"] == STATUS_INPUT
    assert kwargs["shell"] is True and kwargs["timeout"] == 2


def test_a_passthrough_timeout_prints_nothing_but_still_captures(paths):
    with_passthrough(paths)
    runner = FakeRunner(error=subprocess.TimeoutExpired("my-statusline", 2))
    assert run(STATUS_INPUT, paths, NOW, runner) == ""
    assert read_claude_quota(paths.claude_quota).known is True


def test_garbage_input_is_still_relayed(paths):
    with_passthrough(paths)
    assert run("not json", paths, NOW, FakeRunner()) == "relayed"
    assert not paths.claude_quota.exists()


def test_an_unwritable_state_directory_still_relays(paths, tmp_path):
    with_passthrough(paths)
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    broken = paths._replace(state_dir=blocker / "state")
    assert run(STATUS_INPUT, broken, NOW, FakeRunner()) == "relayed"


def test_the_passthrough_really_runs(paths):
    with_passthrough(paths, "cat")
    assert run(STATUS_INPUT, paths, NOW) == STATUS_INPUT
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_statusline.py -q`
Expected: `ModuleNotFoundError: No module named 'model_router.hooks.statusline'`

- [ ] **Step 3: Implement**

`model_router/hooks/statusline.py`:

```python
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
```

- [ ] **Step 4: Wire the `statusline` subcommand into the CLI**

In `model_router/cli.py`, add this import:

```python
from model_router.hooks import statusline
```

Add this function below `_run_hook_command`:

```python
def _run_statusline() -> int:
    try:
        sys.stdout.write(
            statusline.run(sys.stdin.read(), from_env(os.environ), int(time.time()))
        )
    except Exception:
        pass
    return 0
```

Extend the top of `main`:

```python
def main(argv: List[str]) -> int:
    if argv[:1] == ["hook"]:
        return _run_hook_command(argv[1:])
    if argv[:1] == ["statusline"]:
        return _run_statusline()
    args = _parser().parse_args(argv)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_statusline.py -q`
Expected: `6 passed`

- [ ] **Step 6: Check the entry script by hand**

```bash
echo '{"rate_limits":{"five_hour":{"used_percentage":3,"resets_at":9999999999}}}' \
  | MODEL_ROUTER_STATE_DIR=/tmp/model-router-check bin/router statusline
cat /tmp/model-router-check/claude-quota.json
rm -rf /tmp/model-router-check
```

Expected: the first command prints nothing. `cat` prints a JSON object with `captured_at` and `rate_limits.five_hour.used_percentage` of `3.0`.

- [ ] **Step 7: Commit**

```bash
git add model_router/hooks/statusline.py model_router/cli.py tests/test_statusline.py
git commit -m "Add status-line quota capture with passthrough"
```

---

### Task 13: Plugin packaging and the shadow install (spec phase 3)

**Files:**
- Create: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `hooks/hooks.json`
- Test: `tests/test_plugin_files.py`
- Owner's machine, with confirmation: `~/.config/model-router/config.jsonc`, `~/.claude/settings.json`

**Interfaces:**
- Consumes: `bin/router hook pre-agent`, `bin/router statusline`.
- Produces: an installed plugin named `model-router`, running in shadow mode. Task 15 adds a second hook to `hooks/hooks.json`.

Plugin facts, verified against the Claude Code plugin reference:

- A hooks file must wrap its event map in a top-level `"hooks"` key.
- `${CLAUDE_PLUGIN_ROOT}` is substituted in hook commands and in agent Markdown bodies. In a shell-form hook command it must be wrapped in double quotes.
- Files in the plugin's `bin/` directory are on the Bash tool's `PATH` while the plugin is enabled.
- A plugin loaded from a marketplace that was added from a local path is loaded in place, so edits to this repository take effect without reinstalling.

- [ ] **Step 1: Write the failing test**

`tests/test_plugin_files.py`:

```python
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    return json.loads((ROOT / relative).read_text())


def test_plugin_manifest():
    assert load(".claude-plugin/plugin.json")["name"] == "model-router"


def test_marketplace_lists_the_plugin_at_the_repository_root():
    entry = load(".claude-plugin/marketplace.json")["plugins"][0]
    assert (entry["name"], entry["source"]) == ("model-router", "./")


def test_hooks_file_wires_the_pre_launch_hook():
    entry = load("hooks/hooks.json")["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Agent"
    assert entry["hooks"] == [{
        "type": "command",
        "command": '"${CLAUDE_PLUGIN_ROOT}"/bin/router hook pre-agent',
        "timeout": 5,
    }]


def test_entry_script_is_executable():
    assert os.access(str(ROOT / "bin" / "router"), os.X_OK)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_plugin_files.py -q`
Expected: `FileNotFoundError` for `.claude-plugin/plugin.json`

- [ ] **Step 3: Create the plugin files**

`.claude-plugin/plugin.json`:

```json
{
  "name": "model-router",
  "version": "0.1.0",
  "description": "Routes subagent launches across a Claude plan and a ChatGPT plan by preference and remaining quota.",
  "author": { "name": "Larry Zhang" }
}
```

`.claude-plugin/marketplace.json`:

```json
{
  "name": "model-router-local",
  "owner": { "name": "Larry Zhang" },
  "plugins": [
    {
      "name": "model-router",
      "source": "./",
      "description": "Routes subagent launches across a Claude plan and a ChatGPT plan."
    }
  ]
}
```

`hooks/hooks.json`:

```json
{
  "description": "Model router: route subagent launches by preference and quota.",
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}\"/bin/router hook pre-agent",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 4: Run the test and validate the plugin**

Run: `.venv/bin/python -m pytest tests/test_plugin_files.py -q`
Expected: `4 passed`

Run: `claude plugin validate .`
Expected: `Validation passed`. A warning about the `.venv` or `tests` directories is not expected; if the validator reports an unknown field, remove that field and rerun.

- [ ] **Step 5: Commit**

```bash
git add .claude-plugin hooks tests/test_plugin_files.py
git commit -m "Package the router as a Claude Code plugin"
```

- [ ] **Step 6: Install the plugin (owner runs these)**

Stop here and ask the owner to confirm before continuing. The remaining steps change the owner's Claude Code setup.

```bash
claude plugin marketplace add /Users/Larry/GitHub/model-router
claude plugin install model-router@model-router-local
```

Expected: both commands report success. With no config file present the router is in shadow mode, so nothing about any launch changes yet.

- [ ] **Step 7: Create the config in shadow mode and take over the status line (owner confirms)**

Show the owner both changes and get a yes before making them.

Create `~/.config/model-router/config.jsonc`. The passthrough is the owner's current status-line command, copied from `~/.claude/settings.json`:

```jsonc
{
  "mode": "shadow",
  "statusline": {
    "passthrough": "/Users/Larry/GitHub/just-for-fun/.venv/bin/python /Users/Larry/GitHub/just-for-fun/scripts/claude_quota_statusline.py"
  }
}
```

In `~/.claude/settings.json`, change only the `statusLine` command:

```json
"statusLine": {
  "type": "command",
  "command": "/Users/Larry/GitHub/model-router/bin/router statusline"
}
```

Run: `bin/router status`
Expected: `Config: valid, mode shadow`.

- [ ] **Step 8: Verify against a live session**

Start a new Claude Code session in any project. Ask it: `Use an Explore subagent to list the top-level files here.` Then, in a terminal:

Run: `bin/router status`
Expected: `Claude:` now shows a level and two windows, which proves the status-line capture works, and Quota Glass still updates, which proves the passthrough works.

Run: `bin/router log -n 5`
Expected: at least one line for the Explore launch.

Run: `tail -1 ~/Library/Caches/model-router/decisions.jsonl`
Expected: a record with `"mode":"shadow"`, `"agent_type":"Explore"`, `"category":"explore"`, and a non-null `"permission_mode"` (the owner's default is `auto`).

Record the outcome in the commit message of the next step:

- If `permission_mode` is a string: the redirect eligibility rules have what they need. Continue.
- If `permission_mode` is `null`: `PreToolUse` does not carry it on this version. No code change is needed, because a missing mode already blocks every redirect, and the router still makes Claude model changes. Tell the owner that Codex redirects will stay off, and skip the redirect step in Task 16.

- [ ] **Step 9: Record the verification**

```bash
git commit --allow-empty -m "Record live hook verification

permission_mode on PreToolUse(Agent): <string value seen, or null>
status-line capture: working
passthrough to previous status line: working"
```

Replace the bracketed text with what Step 8 showed.

- [ ] **Step 10: Shadow run (owner)**

Leave the router in shadow mode for several days of normal work. Review with `bin/router log -n 50`. For each decision, ask whether you would have made the same choice. Adjust `preferences`, `keywords`, and `agent_categories` in the config until the log matches your own judgment. Do not start Task 16 before this.

---

### Task 14: Codex runner and worker agents

**Files:**
- Create: `model_router/codex_run.py`
- Create: `agents/codex-read.md`, `agents/codex-write.md`
- Modify: `model_router/cli.py`
- Test: `tests/test_codex_run.py`

**Interfaces:**
- Consumes: `config.CODEX_EFFORTS`.
- Produces:
  - `codex_run.split_effort(text: str) -> Tuple[str, str]` (effort, remaining task)
  - `codex_run.build_command(mode: str, effort: str, cwd: str, out_file: str) -> List[str]`
  - `codex_run.run(mode: str, prompt_file: str, cwd: str, runner=subprocess.run) -> Tuple[int, str]` (exit code, text to print)
  - `router codex-run read|write <prompt-file>` on the command line
  - Plugin agents `model-router:codex-read` and `model-router:codex-write`

The sandbox is fixed in code here, not in the agent's prompt (§3.5). Codex facts, verified with `codex exec --help` on this machine (codex-cli 0.160.1):

- `--sandbox` accepts `read-only`, `workspace-write`, and `danger-full-access`. The owner's `~/.codex/config.toml` defaults to `workspace-write`, so the read worker must pass `read-only` explicitly.
- `--cd <DIR>` sets the working directory. `--output-last-message <FILE>` writes the final answer to a file. Passing `-` as the prompt reads it from stdin.
- `-c model_reasoning_effort="xhigh"` overrides the reasoning effort.

The effort arrives as text in a prompt, so it is checked against the known list before it goes anywhere near a command line. An unknown value becomes `medium`.

- [ ] **Step 1: Write the failing tests**

`tests/test_codex_run.py`:

```python
import subprocess
from pathlib import Path

from model_router.codex_run import build_command, run, split_effort

ROOT = Path(__file__).resolve().parents[1]


class FakeCodex:
    def __init__(self, message, returncode=0, stderr="", error=None):
        self.message = message
        self.returncode = returncode
        self.stderr = stderr
        self.error = error
        self.command = None
        self.kwargs = None

    def __call__(self, command, **kwargs):
        self.command, self.kwargs = command, kwargs
        if self.error is not None:
            raise self.error
        if self.message is not None:
            out_file = command[command.index("--output-last-message") + 1]
            Path(out_file).write_text(self.message)
        return subprocess.CompletedProcess(
            command, self.returncode, stdout="", stderr=self.stderr
        )


def prompt_file(tmp_path, text):
    path = tmp_path / "task.md"
    path.write_text(text)
    return str(path)


def test_split_effort():
    assert split_effort("ROUTER_EFFORT: xhigh\nDo it\nnow") == ("xhigh", "Do it\nnow")
    assert split_effort("Do it") == ("medium", "Do it")
    assert split_effort('ROUTER_EFFORT: xhigh" --sandbox danger\nDo it') == (
        "medium", "Do it",
    )


def test_read_command():
    assert build_command("read", "high", "/work", "/tmp/out.txt") == [
        "codex", "exec",
        "--sandbox", "read-only",
        "--cd", "/work",
        "--skip-git-repo-check",
        "-c", 'model_reasoning_effort="high"',
        "--output-last-message", "/tmp/out.txt",
        "-",
    ]


def test_write_command_uses_the_workspace_sandbox():
    command = build_command("write", "xhigh", "/work", "/tmp/out.txt")
    assert command[command.index("--sandbox") + 1] == "workspace-write"


def test_no_command_widens_the_sandbox():
    for mode in ("read", "write"):
        command = build_command(mode, "xhigh", "/work", "/tmp/out.txt")
        assert "danger-full-access" not in command
        assert "--add-dir" not in command
        assert not any(part.startswith("--dangerously") for part in command)


def test_run_returns_the_final_message(tmp_path):
    fake = FakeCodex("All done.\n")
    code, text = run(
        "write", prompt_file(tmp_path, "ROUTER_EFFORT: xhigh\nFix the bug\n"),
        "/work", fake,
    )
    assert (code, text) == (0, "All done.")
    assert fake.kwargs["input"] == "Fix the bug\n"
    assert 'model_reasoning_effort="xhigh"' in fake.command
    assert fake.command[fake.command.index("--cd") + 1] == "/work"


def test_run_reports_a_codex_failure(tmp_path):
    fake = FakeCodex(None, returncode=3, stderr="usage limit reached")
    code, text = run("read", prompt_file(tmp_path, "Look around"), "/work", fake)
    assert code == 3
    assert "Codex failed (exit 3)" in text and "usage limit reached" in text


def test_an_empty_answer_is_a_failure(tmp_path):
    code, text = run(
        "read", prompt_file(tmp_path, "Look around"), "/work", FakeCodex("")
    )
    assert code == 1 and "no answer" in text


def test_bad_mode_missing_file_and_empty_task(tmp_path):
    fake = FakeCodex("unused")
    assert run("admin", prompt_file(tmp_path, "x"), "/work", fake)[0] == 2
    assert run("read", str(tmp_path / "absent.md"), "/work", fake)[0] == 2
    assert run("read", prompt_file(tmp_path, "ROUTER_EFFORT: high\n  \n"), "/work", fake)[0] == 2
    assert fake.command is None


def test_codex_not_installed(tmp_path):
    fake = FakeCodex(None, error=FileNotFoundError("codex"))
    code, text = run("read", prompt_file(tmp_path, "Look around"), "/work", fake)
    assert code == 127 and "could not be started" in text


def test_each_agent_file_pins_its_own_mode():
    read_agent = (ROOT / "agents" / "codex-read.md").read_text()
    write_agent = (ROOT / "agents" / "codex-write.md").read_text()
    assert "codex-run read" in read_agent and "codex-run write" not in read_agent
    assert "codex-run write" in write_agent and "codex-run read" not in write_agent
    for text in (read_agent, write_agent):
        assert "model: haiku" in text
        assert '"${CLAUDE_PLUGIN_ROOT}/bin/router"' in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codex_run.py -q`
Expected: `ModuleNotFoundError: No module named 'model_router.codex_run'`

- [ ] **Step 3: Implement the runner**

`model_router/codex_run.py`:

```python
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
```

- [ ] **Step 4: Create the worker agents**

`agents/codex-read.md`:

```markdown
---
name: codex-read
description: Internal worker for the model router. Forwards one task to Codex in a read-only sandbox and returns Codex's answer. The router's hook selects this agent; do not choose it directly.
model: haiku
tools: Write, Bash
---

You are a thin forwarding wrapper. Codex does the work. Your only job is to hand it the task and return its answer. Do not read the codebase, form a plan, or answer the task yourself.

Steps:

1. Choose one prompt-file path and write it down before doing anything else: `/tmp/model-router-task-<unique>.md`, where you invent the unique part (a timestamp plus a few random characters, for example `/tmp/model-router-task-20261009-8f3a.md`). Never put `$$` or any other shell variable in the path: the Write tool takes the name literally while Bash would expand it, and the two would point at different files.

2. Write the entire task you were given to that path with the Write tool, verbatim. If its first line starts with `ROUTER_EFFORT:`, keep that line exactly as it is. Add nothing.

3. Run exactly one command with the Bash tool, with a timeout of 600000 milliseconds, substituting your literal path for `<PROMPT_FILE>`:

   "${CLAUDE_PLUGIN_ROOT}/bin/router" codex-run read <PROMPT_FILE>

4. Return the command's output verbatim, prefixed by one line: `Answered by Codex (read-only).`

Constraints:

- Run the command in the foreground. Do not background it.
- Do not add flags, change `read`, or call `codex` yourself.
- If the command fails or prints an error, return that output verbatim and stop. Do not retry, and do not do the task yourself.
```

`agents/codex-write.md`:

```markdown
---
name: codex-write
description: Internal worker for the model router. Forwards one task to Codex in a workspace-write sandbox and returns Codex's answer. The router's hook selects this agent; do not choose it directly.
model: haiku
tools: Write, Bash
---

You are a thin forwarding wrapper. Codex does the work, including any edits. Your only job is to hand it the task and return its answer. Do not read the codebase, edit files, run tests, or form a plan yourself.

Steps:

1. Choose one prompt-file path and write it down before doing anything else: `/tmp/model-router-task-<unique>.md`, where you invent the unique part (a timestamp plus a few random characters, for example `/tmp/model-router-task-20261009-8f3a.md`). Never put `$$` or any other shell variable in the path: the Write tool takes the name literally while Bash would expand it, and the two would point at different files.

2. Write the entire task you were given to that path with the Write tool, verbatim. If its first line starts with `ROUTER_EFFORT:`, keep that line exactly as it is. Add nothing.

3. Run exactly one command with the Bash tool, with a timeout of 600000 milliseconds, substituting your literal path for `<PROMPT_FILE>`:

   "${CLAUDE_PLUGIN_ROOT}/bin/router" codex-run write <PROMPT_FILE>

4. Return the command's output verbatim, prefixed by one line: `Done by Codex (workspace-write).`

Constraints:

- Run the command in the foreground. Do not background it.
- Do not add flags, change `write`, or call `codex` yourself.
- If the command fails or prints an error, return that output verbatim and stop. Do not retry, and do not do the task yourself.
```

- [ ] **Step 5: Wire the `codex-run` subcommand into the CLI**

In `model_router/cli.py`, add this import:

```python
from model_router import codex_run
```

In `_parser()`, add before `return parser`:

```python
    worker = commands.add_parser("codex-run", help="run one task through Codex")
    worker.add_argument("mode", choices=("read", "write"))
    worker.add_argument("prompt_file")
```

In `main`, add this branch after the `log` branch and before `return 0`:

```python
    elif args.command == "codex-run":
        code, text = codex_run.run(args.mode, args.prompt_file, os.getcwd())
        print(text)
        return code
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_codex_run.py -q`
Expected: `10 passed`

Run: `claude plugin validate .`
Expected: `Validation passed`

- [ ] **Step 7: One real read-only run (uses a little Codex quota)**

```bash
printf 'ROUTER_EFFORT: low\nReply with exactly the single word: pong\n' > /tmp/model-router-smoke.md
cd /Users/Larry/GitHub/model-router && bin/router codex-run read /tmp/model-router-smoke.md; echo "exit: $?"
rm -f /tmp/model-router-smoke.md
```

Expected: `pong` and `exit: 0`. If Codex rejects a flag, its error names the flag: fix `build_command`, update `test_read_command` to match, and rerun both.

- [ ] **Step 8: Commit**

```bash
git add model_router/codex_run.py model_router/cli.py agents tests/test_codex_run.py
git commit -m "Add the Codex runner and worker agents"
```

---

### Task 15: Prompt-hook guidance

**Files:**
- Create: `model_router/hooks/prompt.py`
- Modify: `model_router/cli.py`, `hooks/hooks.json`, `tests/test_plugin_files.py`
- Test: `tests/test_prompt_hook.py`

**Interfaces:**
- Consumes: `config.load_config`, `config.PLANS`, `policy` (`plan_level`, `window_level`), `quota.read_quotas`, `quota.types` helpers, `state` functions.
- Produces:
  - `hooks.prompt.handle(payload, paths: Paths, now: int, env: Mapping[str, str]) -> Optional[Dict[str, Any]]`
  - `hooks.prompt.guidance_line(quotas, levels, changed, now, config) -> str`
  - `router hook prompt` on the command line, wired as a `UserPromptSubmit` hook

A `UserPromptSubmit` hook adds context by printing `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "..."}}`. The router cannot change the main conversation's model, so this line is how it recommends a switch (§4.6).

It speaks only when a plan's level differs from the level last announced in this session, and only in `enforce` mode. A session that has been told nothing is treated as having been told `normal`, so a quiet start stays quiet.

- [ ] **Step 1: Write the failing tests**

`tests/test_prompt_hook.py`:

```python
from model_router.hooks.prompt import handle
from tests.helpers import NOW, seed_claude, seed_codex


def prompt(session="s1"):
    return {
        "session_id": session,
        "cwd": "/x",
        "permission_mode": "default",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "hello",
    }


def enforce(paths):
    paths.config.write_text('{"mode": "enforce"}')


def context(response):
    return response["hookSpecificOutput"]["additionalContext"]


def test_silent_while_everything_is_normal(paths):
    enforce(paths)
    seed_claude(paths, five=10, week=10)
    assert handle(prompt(), paths, NOW, {}) is None


def test_announces_a_level_change_once(paths):
    enforce(paths)
    seed_claude(paths, five=75, week=10)
    seed_codex(paths, five=5, week=2)
    first = handle(prompt(), paths, NOW, {})
    assert first["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    text = context(first)
    assert text.startswith("Model router: ")
    assert "Claude 5h 75%" in text and "Codex 5h 5%" in text
    assert "Claude is in conserve" in text and "/model sonnet" in text
    assert handle(prompt(), paths, NOW, {}) is None


def test_announces_recovery(paths):
    enforce(paths)
    seed_claude(paths, five=75, week=10)
    handle(prompt(), paths, NOW, {})
    seed_claude(paths, five=10, week=10)
    assert "Claude is back to normal" in context(handle(prompt(), paths, NOW, {}))


def test_critical_suggests_a_lighter_model(paths):
    enforce(paths)
    seed_claude(paths, five=92, week=10)
    text = context(handle(prompt(), paths, NOW, {}))
    assert "Claude is critical" in text and "/model haiku" in text


def test_sessions_are_tracked_separately(paths):
    enforce(paths)
    seed_claude(paths, five=75, week=10)
    assert handle(prompt("s1"), paths, NOW, {}) is not None
    assert handle(prompt("s2"), paths, NOW, {}) is not None


def test_silent_in_shadow_mode(paths):
    paths.config.write_text("{}")
    seed_claude(paths, five=75, week=10)
    assert handle(prompt(), paths, NOW, {}) is None


def test_an_invalid_config_is_announced_once(paths):
    paths.config.write_text("{not json")
    text = context(handle(prompt(), paths, NOW, {}))
    assert "config file is invalid" in text and "router status" in text
    assert handle(prompt(), paths, NOW, {}) is None


def test_environment_off_switch(paths):
    enforce(paths)
    seed_claude(paths, five=75, week=10)
    assert handle(prompt(), paths, NOW, {"MODEL_ROUTER": "off"}) is None
```

Add this test to `tests/test_plugin_files.py`:

```python
def test_hooks_file_wires_the_prompt_hook():
    entry = load("hooks/hooks.json")["hooks"]["UserPromptSubmit"][0]
    assert "matcher" not in entry
    assert entry["hooks"] == [{
        "type": "command",
        "command": '"${CLAUDE_PLUGIN_ROOT}"/bin/router hook prompt',
        "timeout": 5,
    }]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_prompt_hook.py tests/test_plugin_files.py -q`
Expected: `ModuleNotFoundError: No module named 'model_router.hooks.prompt'`, and `KeyError: 'UserPromptSubmit'`

- [ ] **Step 3: Implement**

`model_router/hooks/prompt.py`:

```python
"""UserPromptSubmit adapter: tell the main agent when a plan's level changes."""

from typing import Any, Dict, List, Mapping, Optional

from model_router.config import PLANS, Config, load_config
from model_router.paths import Paths
from model_router.policy import plan_level, window_level
from model_router.quota import read_quotas
from model_router.quota.types import (
    PlanQuota,
    effective_used_pct,
    format_time,
    window_label,
)
from model_router.state import load_state, save_state, session_entry

PLAN_LABELS = {"claude": "Claude", "codex": "Codex"}
LEVEL_TEXT = {
    "normal": "{p} is back to normal.",
    "conserve": "{p} is in conserve: heavy {p} targets are paused.",
    "critical": "{p} is critical: only light {p} targets are used.",
    "exhausted": "{p} is exhausted: no {p} targets are used until it resets.",
}
# The router cannot switch the main conversation's model, so it suggests.
CLAUDE_SUGGESTION = {
    "conserve": "Consider /model sonnet.",
    "critical": "Consider /model haiku, or continue this work in Codex.",
    "exhausted": "Consider /model haiku, or continue this work in Codex.",
}


def _context(text: str) -> Dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        }
    }


def _plan_summary(plan: str, quota: PlanQuota, now: int, config: Config) -> str:
    label = PLAN_LABELS[plan]
    if not quota.known:
        return "%s quota unknown" % label
    parts: List[str] = []
    for window in quota.windows:
        part = "%s %.0f%%" % (window_label(window), effective_used_pct(window, now))
        if window.resets_at and window_level(window, now, config) != "normal":
            part += " (resets %s)" % format_time(window.resets_at)
        parts.append(part)
    return "%s %s" % (label, ", ".join(parts))


def guidance_line(
    quotas: Dict[str, PlanQuota],
    levels: Dict[str, str],
    changed: List[str],
    now: int,
    config: Config,
) -> str:
    summary = "; ".join(_plan_summary(p, quotas[p], now, config) for p in PLANS)
    sentences = [LEVEL_TEXT[levels[p]].format(p=PLAN_LABELS[p]) for p in changed]
    if "claude" in changed and levels["claude"] in CLAUDE_SUGGESTION:
        sentences.append(CLAUDE_SUGGESTION[levels["claude"]])
    return "Model router: %s. %s" % (summary, " ".join(sentences))


def handle(
    payload: Any, paths: Paths, now: int, env: Mapping[str, str]
) -> Optional[Dict[str, Any]]:
    if env.get("MODEL_ROUTER") == "off" or not isinstance(payload, dict):
        return None
    session_id = payload.get("session_id")
    session_id = session_id if isinstance(session_id, str) else ""
    result = load_config(paths.config)

    if result.config is None:
        state = load_state(paths.state)
        entry = session_entry(state, session_id, now)
        if entry.get("config_error_announced"):
            return None
        entry["config_error_announced"] = True
        save_state(paths.state, state, now)
        first = result.errors[0] if result.errors else "unknown error"
        return _context(
            "Model router: the config file is invalid, so routing is off and "
            "launches are unchanged. First error: %s. Run `router status` for "
            "the full list." % first
        )

    config = result.config
    if config.mode != "enforce":
        return None

    quotas = read_quotas(paths)
    levels = {plan: plan_level(quotas[plan], now, config) for plan in PLANS}
    state = load_state(paths.state)
    entry = session_entry(state, session_id, now)
    announced = entry.get("announced")
    if not isinstance(announced, dict):
        announced = {}
    changed = [p for p in PLANS if levels[p] != announced.get(p, "normal")]
    if not changed:
        return None
    entry["announced"] = levels
    save_state(paths.state, state, now)
    return _context(guidance_line(quotas, levels, changed, now, config))
```

- [ ] **Step 4: Wire it into the CLI and the plugin**

In `model_router/cli.py`, change the hooks import and the handler table:

```python
from model_router.hooks import pre_agent, prompt, statusline
```

```python
_HOOK_HANDLERS = {
    "pre-agent": ("pre_agent", pre_agent.handle),
    "prompt": ("prompt", prompt.handle),
}
```

Remove the two older single-module imports (`from model_router.hooks import pre_agent` and `from model_router.hooks import statusline`) that this line replaces.

Replace `hooks/hooks.json` with:

```json
{
  "description": "Model router: route subagent launches by preference and quota.",
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}\"/bin/router hook pre-agent",
            "timeout": 5
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}\"/bin/router hook prompt",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_prompt_hook.py tests/test_plugin_files.py -q`
Expected: `13 passed`

Run: `.venv/bin/python -m pytest -q`
Expected: the whole suite passes.

Run: `claude plugin validate .`
Expected: `Validation passed`

- [ ] **Step 6: Check the entry script by hand**

```bash
echo '{"session_id":"check","hook_event_name":"UserPromptSubmit","prompt":"hi"}' \
  | MODEL_ROUTER_CONFIG=/nonexistent MODEL_ROUTER_STATE_DIR=/tmp/model-router-check bin/router hook prompt
echo "exit: $?"; rm -rf /tmp/model-router-check
```

Expected: no output and `exit: 0` (no config file means shadow mode, which stays silent).

- [ ] **Step 7: Commit**

```bash
git add model_router/hooks/prompt.py model_router/cli.py hooks/hooks.json tests/test_prompt_hook.py tests/test_plugin_files.py
git commit -m "Add prompt-hook guidance on level changes"
```

---

### Task 16: README and go-live (spec phases 4, 5, and 6)

**Files:**
- Create: `README.md`
- Owner's machine, with confirmation at each step: `~/.config/model-router/config.jsonc`, `~/.claude/agents/explorer.md`

**Interfaces:**
- Consumes: everything above. Do not start this task until the shadow run in Task 13 Step 10 is finished and the owner is satisfied with the log.

- [ ] **Step 1: Write the README**

`README.md`:

```markdown
# Model Router

A Claude Code plugin that decides, for each subagent launch, which model
should do the work: a Claude model on your Claude plan, or Codex on your
ChatGPT plan. It decides from your preference list and how much of each
plan's allowance is left.

Design: `docs/superpowers/specs/2026-10-09-model-router-design.md`

## Commands

    bin/router status               quota, level per plan, config state
    bin/router explain "<task>"     dry-run a decision
    bin/router log -n 20            recent decisions and why

## Configuration

Copy `config.example.jsonc` to `~/.config/model-router/config.jsonc`.

The router changes launches only when that file exists, is valid, and sets
`"mode": "enforce"`. With no file it runs in shadow mode: it logs what it
would do and changes nothing. With an invalid file it does nothing at all;
`bin/router status` lists the errors.

## Overrides

- `[route:opus]`, `[route:codex]`, or any target name, in a task prompt:
  use that target. Beats conserve and critical, not exhausted.
- `[route:keep]` in a task prompt: leave this launch alone.
- `MODEL_ROUTER=off` in the environment: off for that session.
- `"mode": "shadow"` or `"off"` in the config: off everywhere.

## Limits

- It routes subagent launches. It cannot change the main conversation's
  model; it suggests a `/model` switch when a plan's level changes.
- A task goes to Codex only when your session's permission mode allows it
  and no file-scoped deny rules exist. See `read_redirect_modes` and
  `write_redirect_modes`.
- Inside a Codex run, Claude Code's Bash rules do not apply to the commands
  Codex runs. The Codex sandbox is the boundary.
- Codex's quota on disk updates only when Codex runs.

## Development

    /usr/bin/python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/python -m pytest

Runtime code uses the Python 3.9 standard library only.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Add the README"
```

- [ ] **Step 3: Phase 4, enforce Claude model changes only (owner confirms)**

Show the owner this change and get a yes. Edit `~/.config/model-router/config.jsonc` so it reads as below, keeping any preference edits made during the shadow run. The two empty lists switch Codex redirects off, so this phase can only change which Claude model a subagent uses:

```jsonc
{
  "mode": "enforce",
  "read_redirect_modes": [],
  "write_redirect_modes": [],
  "statusline": {
    "passthrough": "/Users/Larry/GitHub/just-for-fun/.venv/bin/python /Users/Larry/GitHub/just-for-fun/scripts/claude_quota_statusline.py"
  }
}
```

Run: `bin/router status`
Expected: `Config: valid, mode enforce`.

In a new Claude Code session, ask: `Use an Explore subagent to list the top-level files here.`

Run: `bin/router log -n 3`
Expected: a `set_model` line for the Explore launch with target `haiku`, and the subagent ran normally. No line shows `redirect_codex`.

Use it for at least a day before the next step. If anything misbehaves, set `"mode": "shadow"` and report what the log shows.

- [ ] **Step 4: Phase 5, turn on Codex redirects (owner decides)**

Skip this step if Task 13 Step 8 found `permission_mode` was `null`.

Ask the owner which of these they want. The owner's default permission mode is `auto`, so the choice decides whether write tasks ever reach Codex:

- **Read redirects only.** Remove the `read_redirect_modes` line and keep `"write_redirect_modes": []`. Reviews, planning, and exploration can go to Codex in a read-only sandbox. Implementation stays on Claude.
- **Read and write redirects in `auto` mode.** Remove the `read_redirect_modes` line and set `"write_redirect_modes": ["auto", "acceptEdits", "bypassPermissions"]`. Implementation and debugging can go to Codex, which edits files inside the workspace sandbox without per-command review.
- **The defaults.** Remove both lines. Write redirects happen only in `acceptEdits` and `bypassPermissions` sessions.

Apply the chosen edit, then:

Run: `bin/router status`
Expected: `Config: valid, mode enforce`.

Run: `bin/router explain "Review the retry logic" --permission-mode auto`
Expected: `Decision: redirect_codex -> codex-deep [codex-read]`.

In a new Claude Code session, ask: `Use a general-purpose subagent to review the README for unclear wording. Do not edit anything.`
Expected: the subagent's answer begins `Answered by Codex (read-only).` and `bin/router log -n 3` shows a `redirect_codex` line.

- [ ] **Step 5: Phase 6, retire the manual exhausted switch (owner confirms)**

The owner's `~/.claude/agents/explorer.md` falls back to Codex when a flag file exists. The router now does this automatically, because `explorer` is a redirectable agent type. Show the owner the edits below and get a yes.

```bash
cp ~/.claude/agents/explorer.md ~/.claude/agents/explorer.md.bak
```

Then edit `~/.claude/agents/explorer.md`:

1. In the frontmatter `description`, delete the final sentence: ` Falls back to Codex (latest model, xhigh) when Claude usage is exhausted.`
2. Change the frontmatter `tools` line to `tools: Read, Grep, Glob, Bash`.
3. Delete the bullet that begins `- The only file you may ever write is the temporary Codex prompt file`.
4. Delete everything from the heading `## Step 0 — pick your mode` up to, but not including, the heading `## Mode A — explore with Claude (normal)`.
5. Rename that heading to `## How to explore`.
6. Delete everything from the heading `## Mode B — forward to Codex (latest model, xhigh)` to the end of the file.

Remove the flag file if it exists:

```bash
rm -f ~/.claude/.usage-exhausted
```

In a new Claude Code session, ask: `Use the explorer subagent to find where this project's tests live.`
Expected: it answers normally, and `bin/router log -n 1` shows a decision for agent type `explorer`.

Once the owner is satisfied, delete the backup: `rm ~/.claude/agents/explorer.md.bak`.

- [ ] **Step 6: Final check**

Run: `.venv/bin/python -m pytest -q`
Expected: the whole suite passes.

Run: `git status --short`
Expected: no output.
