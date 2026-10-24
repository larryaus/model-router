import stat

from model_router.decision_log import _rotate, append_record, read_all, read_recent


def test_append_and_read_recent(tmp_path):
    log = tmp_path / "state" / "decisions.jsonl"
    for index in range(5):
        append_record(log, {"n": index})
    assert [record["n"] for record in read_recent(log, 3)] == [2, 3, 4]
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_rotates_at_the_size_limit(tmp_path):
    log = tmp_path / "decisions.jsonl"
    append_record(log, {"n": 1}, max_bytes=5)
    append_record(log, {"n": 2}, max_bytes=5)
    assert (tmp_path / "decisions.jsonl.1").exists()
    assert [record["n"] for record in read_recent(log, 10)] == [2]


def test_keeps_five_rotated_files_and_drops_the_oldest(tmp_path):
    log = tmp_path / "decisions.jsonl"
    for index in range(8):
        append_record(log, {"n": index}, max_bytes=5)
    assert [record["n"] for record in read_recent(log, 10)] == [7]
    assert not (tmp_path / "decisions.jsonl.6").exists()
    assert [record["n"] for record in read_all(log)] == [2, 3, 4, 5, 6, 7]


def test_a_rotation_rechecks_the_size_under_the_lock(tmp_path):
    # Another process rotated first: the live file is small again.
    log = tmp_path / "decisions.jsonl"
    append_record(log, {"n": 1})
    _rotate(log, max_bytes=1000, keep=5)
    assert not (tmp_path / "decisions.jsonl.1").exists()
    assert [record["n"] for record in read_recent(log, 10)] == [1]


def test_read_all_tolerates_gaps_and_a_missing_log(tmp_path):
    log = tmp_path / "decisions.jsonl"
    assert read_all(log) == []
    (tmp_path / "decisions.jsonl.3").write_text('{"n": 1}\n')
    log.write_text('{"n": 2}\nnot json\n')
    assert [record["n"] for record in read_all(log)] == [1, 2]


def test_read_recent_skips_bad_lines_and_missing_files(tmp_path):
    log = tmp_path / "decisions.jsonl"
    assert read_recent(log, 5) == []
    log.write_text('{"n": 1}\nnot json\n[1]\n{"n": 2}\n')
    assert [record["n"] for record in read_recent(log, 10)] == [1, 2]
    assert read_recent(log, 0) == []
