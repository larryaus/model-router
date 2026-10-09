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


def test_settings_files_also_walk_up_from_the_project_directory(tmp_path):
    files = settings_files(
        str(tmp_path / "other"),
        tmp_path / ".claude",
        tmp_path / "managed.json",
        str(tmp_path / "work" / "repo"),
    )
    assert tmp_path / "work" / "repo" / ".claude" / "settings.json" in files
    assert tmp_path / "work" / ".claude" / "settings.local.json" in files
    assert tmp_path / "other" / ".claude" / "settings.json" in files
    assert len(files) == len(set(files))


@pytest.mark.parametrize(
    "rule", ["MultiEdit(src/**)", "NotebookEdit", "Glob(secrets/**)", "Grep(secrets/**)"]
)
def test_other_file_tools_count(tmp_path, rule):
    path = write(tmp_path / "s.json", {"permissions": {"deny": [rule]}})
    assert has_file_deny_rules([path]) is True


def test_a_file_scoped_ask_rule_counts(tmp_path):
    path = write(tmp_path / "s.json", {"permissions": {"ask": ["Edit(infra/**)"]}})
    assert has_file_deny_rules([path]) is True


def test_a_bash_only_ask_rule_does_not_count(tmp_path):
    path = write(tmp_path / "s.json", {"permissions": {"ask": ["Bash(make deploy:*)"]}})
    assert has_file_deny_rules([path]) is False


def test_a_malformed_ask_list_returns_none(tmp_path):
    path = write(tmp_path / "s.json", {"permissions": {"ask": "Edit"}})
    assert has_file_deny_rules([path]) is None
