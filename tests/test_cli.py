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
