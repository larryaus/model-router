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
        # Claude Code keeps its user settings here when this is set.
        claude_home=Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude"),
        managed_settings=Path(MANAGED_SETTINGS),
    )
