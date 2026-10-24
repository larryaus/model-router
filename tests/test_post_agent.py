import json

import pytest

from model_router.decision_log import read_recent
from model_router.hooks import post_agent, pre_agent
from tests.helpers import NOW

INPUT = {
    "subagent_type": "general-purpose",
    "description": "Implement the cache",
    "prompt": "Implement the cache layer",
}


def configure(paths, **verify):
    settings = {"verify": dict({"command": "touch ran"}, **verify)}
    paths.config.write_text(json.dumps(settings))


def launch(paths, cwd, tool_input=INPUT, tool_use_id="toolu_1"):
    """Run the pre-launch hook, which logs the decision the post hook looks up."""
    pre_agent.handle({
        "session_id": "s1",
        "cwd": str(cwd),
        "permission_mode": "acceptEdits",
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_use_id": tool_use_id,
        "tool_input": dict(tool_input),
    }, paths, NOW, {})


def finish(paths, session_dir, env=None, tool_use_id="toolu_1", **extra):
    payload = {
        "session_id": "s1",
        "cwd": str(session_dir),
        "permission_mode": "acceptEdits",
        "hook_event_name": "PostToolUse",
        "tool_name": "Agent",
        "tool_use_id": tool_use_id,
        "tool_input": dict(INPUT),
        "tool_response": {"status": "completed", "resolvedModel": "claude-sonnet-5-5"},
        "duration_ms": 4200,
    }
    payload.update(extra)
    return post_agent.handle(payload, paths, NOW + 60, env or {})


def outcomes(paths):
    return [r for r in read_recent(paths.log, 50) if r.get("event") == "outcome"]


def test_a_failing_verify_logs_what_judge_would_do(paths, tmp_path):
    configure(paths, command="false")
    launch(paths, tmp_path)
    assert finish(paths, tmp_path) is None
    [record] = outcomes(paths)
    assert record["verify"] == "fail"
    assert (record["outcome"], record["target"], record["next_target"]) == (
        "retry", "codex-deep", "codex-deep",
    )
    assert record["reason"] == "verify failed on codex-deep; retry it"
    assert record["category"] == "implement" and record["mode"] == "shadow"
    assert record["resolved_model"] == "claude-sonnet-5-5"
    assert record["duration_ms"] == 4200
    assert record["tool_use_id"] == "toolu_1" and record["session_id"] == "s1"
    assert record["ts"] == NOW + 60


def test_a_passing_verify_is_logged_as_a_pass(paths, tmp_path):
    configure(paths, command="true")
    launch(paths, tmp_path)
    finish(paths, tmp_path)
    [record] = outcomes(paths)
    assert (record["verify"], record["outcome"]) == ("pass", "pass")


def test_the_command_runs_in_the_sessions_directory(paths, tmp_path):
    configure(paths)
    launch(paths, tmp_path)
    finish(paths, tmp_path)
    assert (tmp_path / "ran").exists()


def test_the_hook_never_responds_even_in_enforce_mode(paths, tmp_path):
    paths.config.write_text(json.dumps(
        {"mode": "enforce", "verify": {"command": "false"}}
    ))
    launch(paths, tmp_path)
    assert finish(paths, tmp_path) is None
    assert outcomes(paths)[0]["outcome"] == "retry"
    assert outcomes(paths)[0]["mode"] == "enforce"


def test_the_mode_logged_is_the_one_the_launch_was_routed_in(paths, tmp_path):
    # A shadow launch did not run its target, even if enforce is on by its end.
    configure(paths, command="false")
    launch(paths, tmp_path)
    paths.config.write_text(json.dumps(
        {"mode": "enforce", "verify": {"command": "false"}}
    ))
    finish(paths, tmp_path)
    assert outcomes(paths)[0]["mode"] == "shadow"


def test_a_command_that_cannot_run_is_logged_as_unknown(paths, tmp_path):
    configure(paths, command="no-such-command-model-router")
    launch(paths, tmp_path)
    finish(paths, tmp_path)
    [record] = outcomes(paths)
    assert (record["verify"], record["outcome"]) == ("unknown", "keep")


def test_retry_same_comes_from_the_config(paths, tmp_path):
    # No retry, and codex-deep is already heavy, so there is nowhere to go.
    configure(paths, command="false", retry_same=0)
    launch(paths, tmp_path)
    finish(paths, tmp_path)
    assert outcomes(paths)[0]["outcome"] == "human"


def _skip_cases():
    review = dict(INPUT, description="Review the cache", prompt="Review the cache")
    explore = dict(INPUT, subagent_type="Explore")
    return [
        ("verify off", {"command": None}, {}, {}),
        ("router off", {}, {}, {"env": {"MODEL_ROUTER": "off"}}),
        ("nested", {}, {}, {"agent_id": "a1"}),
        ("background launch", {}, {}, {"tool_response": {"status": "async_launched"}}),
        ("no response", {}, {}, {"tool_response": None}),
        ("other decision", {}, {}, {"tool_use_id": "toolu_other"}),
        ("relative cwd", {}, {}, {"cwd": "project"}),
        ("not a verify category", {}, {"tool_input": review}, {}),
        ("read-only agent", {"categories": ["implement", "explore"]},
         {"tool_input": explore}, {}),
        ("passthrough agent", {},
         {"tool_input": dict(INPUT, subagent_type="codex-review")}, {}),
    ]


@pytest.mark.parametrize(
    "verify,launch_args,finish_args",
    [case[1:] for case in _skip_cases()],
    ids=[case[0] for case in _skip_cases()],
)
def test_nothing_runs_and_nothing_is_logged(paths, tmp_path, verify, launch_args, finish_args):
    configure(paths, **verify)
    launch(paths, tmp_path, **launch_args)
    assert finish(paths, tmp_path, **finish_args) is None
    assert not (tmp_path / "ran").exists()
    assert outcomes(paths) == []


def test_mode_off_verifies_nothing(paths, tmp_path):
    configure(paths)
    launch(paths, tmp_path)
    paths.config.write_text(json.dumps({"mode": "off", "verify": {"command": "touch ran"}}))
    finish(paths, tmp_path)
    assert not (tmp_path / "ran").exists() and outcomes(paths) == []


def test_an_invalid_config_verifies_nothing(paths, tmp_path):
    configure(paths)
    launch(paths, tmp_path)
    paths.config.write_text('{"mdoe": "x"}')
    finish(paths, tmp_path)
    assert not (tmp_path / "ran").exists() and outcomes(paths) == []
