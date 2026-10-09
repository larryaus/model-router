from datetime import datetime, timezone

from model_router.quota import read_quotas
from model_router.quota.codex import read_codex_quota
from model_router.quota.types import Window, unknown
from tests.helpers import NOW, codex_line, seed_claude, seed_codex, write_rollout

P = {"used_percent": 59.0, "window_minutes": 300, "resets_at": 1791338519}
S = {"used_percent": 14.0, "window_minutes": 10080, "resets_at": 1791802257}


def test_reads_the_last_reading_of_the_newest_file(tmp_path):
    write_rollout(tmp_path, "old", [codex_line(dict(P, used_percent=1.0), S)], mtime=1000)
    write_rollout(
        tmp_path,
        "new",
        ['{"type":"other"}', codex_line(dict(P, used_percent=20.0), S),
         codex_line(P, S), '{"type":"tail"}'],
        mtime=2000,
    )
    quota = read_codex_quota(tmp_path)
    assert quota.plan == "codex" and quota.known is True
    assert quota.windows == [Window(59.0, 300, 1791338519), Window(14.0, 10080, 1791802257)]
    expected = datetime(2026, 10, 7, 1, 28, 13, 535000, tzinfo=timezone.utc)
    assert quota.captured_at == int(expected.timestamp())


def test_falls_back_to_an_older_file(tmp_path):
    write_rollout(tmp_path, "old", [codex_line(P, S)], mtime=1000)
    write_rollout(tmp_path, "new", ['{"type":"other"}'], mtime=2000)
    assert read_codex_quota(tmp_path).windows[0] == Window(59.0, 300, 1791338519)


def test_skips_lines_that_do_not_parse(tmp_path):
    write_rollout(
        tmp_path,
        "new",
        [codex_line(P, S), '{"payload": {"rate_limits": ', 'garbage "rate_limits"'],
        mtime=2000,
    )
    assert read_codex_quota(tmp_path).known is True


def test_reads_only_the_tail(tmp_path):
    filler = ['{"type":"filler","pad":"%s"}' % ("x" * 200)] * 50
    write_rollout(tmp_path, "big", [codex_line(P, S)] + filler, mtime=2000)
    assert read_codex_quota(tmp_path, tail_bytes=1024) == unknown("codex")
    assert read_codex_quota(tmp_path).known is True


def test_a_null_window_is_skipped(tmp_path):
    write_rollout(tmp_path, "new", [codex_line(None, S)], mtime=2000)
    assert read_codex_quota(tmp_path).windows == [Window(14.0, 10080, 1791802257)]


def test_missing_or_empty_directory_is_unknown(tmp_path):
    assert read_codex_quota(tmp_path / "absent") == unknown("codex")
    assert read_codex_quota(tmp_path) == unknown("codex")


def test_max_files_limits_the_search(tmp_path):
    write_rollout(tmp_path, "a", [codex_line(P, S)], mtime=1000)
    write_rollout(tmp_path, "b", ['{"type":"other"}'], mtime=2000)
    write_rollout(tmp_path, "c", ['{"type":"other"}'], mtime=3000)
    assert read_codex_quota(tmp_path, max_files=2) == unknown("codex")
    assert read_codex_quota(tmp_path, max_files=3).known is True


def test_read_quotas_returns_both_plans(paths):
    seed_claude(paths, five=30, week=10)
    seed_codex(paths, five=5, week=2)
    quotas = read_quotas(paths)
    assert sorted(quotas) == ["claude", "codex"]
    assert quotas["claude"].windows[0].used_pct == 30.0
    assert quotas["codex"].windows[0].used_pct == 5.0
    assert quotas["claude"].captured_at == NOW
