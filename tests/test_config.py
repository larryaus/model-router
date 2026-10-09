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


def test_a_codex_target_may_name_a_model(tmp_path):
    result = load_config(write(tmp_path, """{
      "targets": {"codex-deep": {"plan": "codex", "effort": "xhigh",
                                 "weight": "heavy", "model": "gpt-6-astra"}}
    }"""))
    assert result.status == "valid"
    assert result.config.targets["codex-deep"].model == "gpt-6-astra"
    assert result.config.targets["codex"].model is None


@pytest.mark.parametrize("model", ['"two words"', '"--sandbox"', "7", '""'])
def test_a_codex_model_name_is_checked(tmp_path, model):
    text = '{"targets": {"x": {"plan": "codex", "effort": "low", "weight": "light", "model": %s}}}' % model
    result = load_config(write(tmp_path, text))
    assert result.status == "invalid"
    assert any("targets.x.model" in error for error in result.errors), result.errors


def test_statusline_passthrough_survives_an_invalid_config(tmp_path):
    path = write(tmp_path, '{"mdoe": "x", "statusline": {"passthrough": "echo hi"}}')
    assert load_config(path).status == "invalid"
    assert raw_statusline_passthrough(path) == "echo hi"


def test_statusline_passthrough_absent_or_unreadable(tmp_path):
    assert raw_statusline_passthrough(tmp_path / "absent.jsonc") is None
    assert raw_statusline_passthrough(write(tmp_path, "{not json")) is None
    assert raw_statusline_passthrough(write(tmp_path, "{}")) is None
