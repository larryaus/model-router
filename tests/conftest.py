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
