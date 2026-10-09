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


def test_claude_config_dir_is_honoured(tmp_path):
    paths = from_env({"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": "/elsewhere/claude"})
    assert paths.claude_home == Path("/elsewhere/claude")
