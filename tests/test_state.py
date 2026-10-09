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
