import stat

from model_router.fsutil import atomic_write_text


def test_creates_parent_and_owner_only_file(tmp_path):
    target = tmp_path / "nested" / "file.json"
    atomic_write_text(target, "hello\n")
    assert target.read_text() == "hello\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_replaces_existing_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "file.json"
    atomic_write_text(target, "one")
    atomic_write_text(target, "two")
    assert target.read_text() == "two"
    assert [entry.name for entry in tmp_path.iterdir()] == ["file.json"]
