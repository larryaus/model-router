import json

import pytest

from model_router.quota.claude import (
    extract_rate_limits,
    read_claude_quota,
    write_snapshot,
)
from model_router.quota.types import (
    PlanQuota,
    Window,
    effective_used_pct,
    unknown,
    window_label,
)

PAYLOAD = {
    "session_id": "secret-session",
    "transcript_path": "/private/path",
    "rate_limits": {
        "five_hour": {"used_percentage": 42.5, "resets_at": 1800001000},
        "seven_day": {"used_percentage": 7, "resets_at": 1800500000},
    },
}


def test_snapshot_keeps_only_quota_fields(tmp_path):
    target = tmp_path / "state" / "claude-quota.json"
    assert write_snapshot(PAYLOAD, target, 1800000000) is True
    assert json.loads(target.read_text()) == {
        "captured_at": 1800000000,
        "rate_limits": {
            "five_hour": {"used_percentage": 42.5, "resets_at": 1800001000},
            "seven_day": {"used_percentage": 7.0, "resets_at": 1800500000},
        },
    }


def test_snapshot_not_written_without_rate_limits(tmp_path):
    target = tmp_path / "claude-quota.json"
    assert write_snapshot({"session_id": "x"}, target, 1) is False
    assert not target.exists()


def test_read_round_trip(tmp_path):
    target = tmp_path / "claude-quota.json"
    write_snapshot(PAYLOAD, target, 1800000000)
    assert read_claude_quota(target) == PlanQuota(
        "claude",
        [Window(42.5, 300, 1800001000), Window(7.0, 10080, 1800500000)],
        1800000000,
        True,
    )


def test_missing_or_malformed_snapshot_is_unknown(tmp_path):
    assert read_claude_quota(tmp_path / "absent.json") == unknown("claude")
    bad = tmp_path / "bad.json"
    for text in ("{not json", "[]", '{"rate_limits": {}}'):
        bad.write_text(text)
        assert read_claude_quota(bad) == unknown("claude")


@pytest.mark.parametrize("value", [True, "50", -1, float("nan"), None])
def test_invalid_percentages_are_dropped(value):
    payload = {"rate_limits": {"five_hour": {"used_percentage": value, "resets_at": 1}}}
    assert extract_rate_limits(payload) == {}


def test_a_percentage_above_100_is_clamped_not_dropped():
    payload = {"rate_limits": {"five_hour": {"used_percentage": 101, "resets_at": 1}}}
    assert extract_rate_limits(payload) == {
        "five_hour": {"used_percentage": 100.0, "resets_at": 1}
    }


def _limits(**windows):
    return {
        "rate_limits": {
            key: {"used_percentage": used, "resets_at": resets}
            for key, (used, resets) in windows.items()
        }
    }


def test_a_partial_payload_keeps_the_other_window(tmp_path):
    target = tmp_path / "claude-quota.json"
    write_snapshot(_limits(five_hour=(10, 2000), seven_day=(99, 5000)), target, 1000)
    write_snapshot(_limits(five_hour=(20, 2000)), target, 1100)
    quota = read_claude_quota(target)
    assert quota.windows == [Window(20.0, 300, 2000), Window(99.0, 10080, 5000)]
    assert quota.captured_at == 1100


def test_a_window_that_has_reset_is_not_carried_over(tmp_path):
    target = tmp_path / "claude-quota.json"
    write_snapshot(_limits(five_hour=(10, 2000), seven_day=(99, 1050)), target, 1000)
    write_snapshot(_limits(five_hour=(20, 2000)), target, 1100)
    assert read_claude_quota(target).windows == [Window(20.0, 300, 2000)]


def test_a_window_without_a_reset_time_is_not_carried_over(tmp_path):
    target = tmp_path / "claude-quota.json"
    write_snapshot(_limits(five_hour=(10, 2000), seven_day=(99, None)), target, 1000)
    write_snapshot(_limits(five_hour=(20, 2000)), target, 1100)
    assert read_claude_quota(target).windows == [Window(20.0, 300, 2000)]


def test_a_window_without_a_reset_time_expires_one_window_after_capture(tmp_path):
    target = tmp_path / "claude-quota.json"
    write_snapshot(_limits(seven_day=(99, None)), target, 1000)
    window = read_claude_quota(target).windows[0]
    assert window == Window(99.0, 10080, 1000 + 10080 * 60)
    assert effective_used_pct(window, now=1000 + 10080 * 60) == 0.0


def test_missing_reset_time_is_kept_as_none():
    limits = extract_rate_limits({"rate_limits": {"seven_day": {"used_percentage": 5}}})
    assert limits == {"seven_day": {"used_percentage": 5.0, "resets_at": None}}


def test_passed_reset_counts_as_zero():
    assert effective_used_pct(Window(80.0, 300, 100), now=100) == 0.0
    assert effective_used_pct(Window(80.0, 300, 101), now=100) == 80.0
    assert effective_used_pct(Window(80.0, 300, None), now=100) == 80.0


def test_window_labels():
    assert window_label(Window(1.0, 300, None)) == "5h"
    assert window_label(Window(1.0, 10080, None)) == "weekly"
    assert window_label(Window(1.0, 60, None)) == "60m"
