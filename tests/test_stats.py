from model_router.stats import render_stats
from tests.helpers import NOW, default_config

CONFIG = default_config()


def decision(category, action, target=None, ts=NOW):
    return {"ts": ts, "category": category, "action": action, "target": target}


def result(category, target, verify, outcome, mode="enforce", model=None,
           duration_ms=60000, ts=NOW):
    return {
        "ts": ts, "event": "outcome", "category": category, "target": target,
        "verify": verify, "outcome": outcome, "mode": mode,
        "resolved_model": model, "duration_ms": duration_ms,
    }


def row(text, *starts):
    """The table line whose cells begin with these values."""
    for line in text.splitlines():
        if line.split()[:len(starts)] == list(starts):
            return line.split()
    raise AssertionError("no row %r in:\n%s" % (starts, text))


def test_empty_log():
    assert render_stats([], CONFIG, NOW) == "No decisions logged yet."
    assert render_stats([], CONFIG, NOW, days=7) == "No decisions logged in the last 7 days."


def test_routing_counts_actions_and_target_weights():
    text = render_stats([
        decision("implement", "set_model", "sonnet"),
        decision("implement", "redirect_codex", "codex-deep"),
        decision("implement", "keep"),
        decision("implement", "deny"),
        decision("implement", "set_model", "renamed-target"),
    ], CONFIG, NOW)
    # launches, routed, kept, denied, heavy, medium, light
    assert row(text, "implement") == ["implement", "5", "3", "1", "1", "1", "1", "0"]
    assert "No verified results yet" in text


def test_categories_follow_the_config_order():
    text = render_stats([
        decision("debug", "keep"), decision("plan", "keep"), decision("zzz", "keep"),
    ], CONFIG, NOW)
    lines = [line.split()[0] for line in text.splitlines() if line.startswith("  ")]
    assert lines == ["category", "plan", "debug", "zzz"]


def test_results_are_grouped_by_what_ran():
    text = render_stats([
        result("implement", "sonnet", "pass", "pass", duration_ms=130000),
        result("implement", "sonnet", "fail", "retry", duration_ms=95000),
        result("implement", "sonnet", "unknown", "keep", duration_ms=60000),
        result("implement", "codex-deep", "pass", "pass", duration_ms=240000),
    ], CONFIG, NOW)
    # results, pass, fail, unknown, pass rate (unknown left out), median time
    assert row(text, "implement", "sonnet")[2:] == ["3", "1", "1", "1", "50%", "1m35s"]
    assert row(text, "implement", "codex-deep")[2:] == ["1", "1", "0", "0", "100%", "4m00s"]
    assert "shadow mode" not in text


def test_shadow_results_count_the_model_that_actually_ran():
    text = render_stats([
        result("debug", "opus", "fail", "retry", mode="shadow", model="claude-opus-5-5",
               duration_ms=45000),
        result("debug", "opus", "pass", "pass", mode="shadow", model=None),
    ], CONFIG, NOW)
    assert row(text, "debug", "claude-opus-5-5", "*")[3:] == ["1", "0", "1", "0", "0%", "45s"]
    assert row(text, "debug", "unknown", "*")
    assert "* shadow mode" in text


def test_a_result_with_only_unknowns_has_no_pass_rate():
    text = render_stats([result("implement", "sonnet", "unknown", "keep",
                                duration_ms=None)], CONFIG, NOW)
    assert row(text, "implement", "sonnet")[-2:] == ["-", "-"]


def test_next_steps_are_counted_per_category():
    text = render_stats([
        result("implement", "sonnet", "fail", "retry"),
        result("implement", "sonnet", "fail", "escalate"),
        result("implement", "opus", "fail", "human"),
        result("implement", "opus", "pass", "pass"),
    ], CONFIG, NOW)
    next_steps = text.split("What the router would do next")[1]
    # pass, retry, escalate, human, keep
    assert row(next_steps, "implement") == ["implement", "1", "1", "1", "1", "0"]


def test_days_keeps_only_recent_records():
    old = NOW - 10 * 24 * 3600
    records = [decision("plan", "keep", ts=old), decision("implement", "keep")]
    text = render_stats(records, CONFIG, NOW, days=7)
    assert text.startswith("Last 7 days: 1 decisions, 0 verified results")
    assert "plan" not in text
    assert render_stats(records, CONFIG, NOW).startswith("Since ")


def test_hook_errors_are_reported():
    text = render_stats([
        decision("plan", "keep"), {"ts": NOW, "event": "hook_error", "error": "boom"},
    ], CONFIG, NOW)
    assert "Hook errors: 1" in text
    assert "Hook errors" not in render_stats([decision("plan", "keep")], CONFIG, NOW)
