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
