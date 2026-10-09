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
