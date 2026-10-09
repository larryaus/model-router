import pytest

from model_router.policy import (
    plan_level,
    projected_pct,
    weekly_projection,
    window_level,
)
from model_router.quota.types import Window, unknown
from tests.helpers import NOW, default_config, quota, window

CONFIG = default_config()


@pytest.mark.parametrize("used,expected", [
    (0, "normal"), (69.9, "normal"),
    (70, "conserve"), (89.9, "conserve"),
    (90, "critical"), (97.9, "critical"),
    (98, "exhausted"), (100, "exhausted"),
])
def test_threshold_boundaries(used, expected):
    assert window_level(window(used), NOW, CONFIG) == expected


def test_a_passed_reset_counts_as_zero():
    assert window_level(Window(99.0, 300, NOW - 1), NOW, CONFIG) == "normal"
    assert window_level(Window(99.0, 300, NOW), NOW, CONFIG) == "normal"


def test_projection_is_undefined_before_warmup():
    assert projected_pct(window(14, elapsed=0.14), NOW, 0.15) is None


def test_projection_after_warmup():
    assert projected_pct(window(30, elapsed=0.5), NOW, 0.15) == pytest.approx(60.0)


def test_projection_reaching_100_means_conserve():
    assert window_level(window(50, elapsed=0.5), NOW, CONFIG) == "conserve"
    assert window_level(window(49, elapsed=0.5), NOW, CONFIG) == "normal"


def test_projection_never_divides_by_zero():
    no_reset = Window(50.0, 300, None)
    at_window_start = Window(50.0, 300, NOW + 300 * 60)
    reset_beyond_window = Window(50.0, 300, NOW + 999_999)
    zero_length = Window(50.0, 0, NOW + 60)
    for case in (no_reset, at_window_start, reset_beyond_window, zero_length):
        assert projected_pct(case, NOW, 0.0) is None
        assert window_level(case, NOW, CONFIG) == "normal"


def test_plan_level_is_the_worst_window():
    assert plan_level(quota("claude", five=10, week=95), NOW, CONFIG) == "critical"
    assert plan_level(quota("claude", five=75, week=10), NOW, CONFIG) == "conserve"
    assert plan_level(quota("claude", five=10, week=10), NOW, CONFIG) == "normal"


def test_unknown_quota_is_normal():
    assert plan_level(unknown("codex"), NOW, CONFIG) == "normal"


def test_weekly_projection_uses_the_weekly_window_only():
    q = quota("claude", five=90, week=45, five_elapsed=0.5, week_elapsed=0.5)
    assert weekly_projection(q, NOW, CONFIG) == pytest.approx(90.0)
    assert weekly_projection(quota("claude", week=45), NOW, CONFIG) is None
    assert weekly_projection(unknown("claude"), NOW, CONFIG) is None
