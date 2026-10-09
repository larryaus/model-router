import pytest

from model_router.classify import Classification
from model_router.policy import Launch, decide, needs_write, redirect_block_reason
from model_router.quota.types import unknown
from tests.helpers import NOW, default_config, quota

CONFIG = default_config()


def launch(agent_type="general-purpose", mode="acceptEdits", nested=False, deny=False):
    return Launch(agent_type, mode, nested, deny)


def run(category, claude=None, codex=None, override=None, **launch_args):
    quotas = {
        "claude": claude or quota("claude"),
        "codex": codex or quota("codex"),
    }
    return decide(
        Classification(category, "keyword", override),
        launch(**launch_args),
        quotas,
        CONFIG,
        NOW,
    )


def test_normal_quota_takes_the_first_preference():
    decision = run("plan")
    assert (decision.action, decision.target, decision.worker) == ("set_model", "opus", None)
    assert decision.levels == {"claude": "normal", "codex": "normal"}
    assert decision.reason == "plan -> opus"


def test_a_codex_first_preference_redirects_with_the_write_worker():
    decision = run("implement")
    assert (decision.action, decision.target, decision.worker) == (
        "redirect_codex", "codex-deep", "codex-write",
    )


def test_conserve_skips_heavy_targets_on_that_plan():
    decision = run("plan", claude=quota("claude", five=75))
    assert (decision.action, decision.target, decision.worker) == (
        "redirect_codex", "codex-deep", "codex-read",
    )
    assert "opus skipped: claude conserve" in decision.reason


def test_falls_back_to_the_lightest_allowed_target():
    decision = run("plan", claude=quota("claude", five=92), codex=quota("codex", five=99))
    assert (decision.action, decision.target) == ("set_model", "haiku")
    assert "fallback" in decision.reason


def test_denies_when_nothing_is_allowed():
    decision = run("plan", claude=quota("claude", five=99), codex=quota("codex", five=99))
    assert decision.action == "deny" and decision.target is None
    assert "earliest reset" in decision.reason


@pytest.mark.parametrize(
    "agent", ["codex-review", "fork", "model-router:codex-read", "codex-write"]
)
def test_passthrough_agents_are_kept(agent):
    assert run("implement", agent_type=agent).action == "keep"


def test_keep_tag():
    assert run("implement", override="keep").action == "keep"


def test_override_beats_conserve_and_critical():
    decision = run("explore", claude=quota("claude", five=92), override="opus")
    assert (decision.action, decision.target) == ("set_model", "opus")
    assert "override" in decision.reason


def test_override_still_obeys_exhausted():
    decision = run("explore", claude=quota("claude", five=99), override="opus")
    assert decision.target == "codex"
    assert "override opus rejected: claude exhausted" in decision.reason


def test_override_to_codex_obeys_redirect_eligibility():
    decision = run("implement", override="codex-deep", mode="default")
    assert (decision.action, decision.target) == ("set_model", "sonnet")
    assert "override codex-deep rejected" in decision.reason


def test_unknown_tag_is_ignored_with_a_note():
    decision = run("plan", override="gpt9")
    assert decision.target == "opus"
    assert "unknown route tag 'gpt9' ignored" in decision.reason


@pytest.mark.parametrize("mode,redirects", [
    ("plan", False), ("default", False), ("auto", False), ("dontAsk", False),
    (None, False), ("weird", False),
    ("acceptEdits", True), ("bypassPermissions", True),
])
def test_write_redirect_by_permission_mode(mode, redirects):
    decision = run("implement", mode=mode)
    if redirects:
        assert (decision.action, decision.worker) == ("redirect_codex", "codex-write")
    else:
        assert (decision.action, decision.target, decision.worker) == (
            "set_model", "sonnet", None,
        )


@pytest.mark.parametrize("mode,redirects", [
    ("plan", False), ("dontAsk", False), (None, False), ("weird", False),
    ("default", True), ("auto", True),
    ("acceptEdits", True), ("bypassPermissions", True),
])
def test_read_redirect_by_permission_mode(mode, redirects):
    decision = run("review", mode=mode)
    if redirects:
        assert (decision.action, decision.target, decision.worker) == (
            "redirect_codex", "codex-deep", "codex-read",
        )
    else:
        assert (decision.action, decision.target, decision.worker) == (
            "set_model", "opus", None,
        )


@pytest.mark.parametrize("launch_args,fragment", [
    ({"nested": True}, "nested launch"),
    ({"agent_type": "my-custom-agent"}, "not redirectable"),
    ({"deny": True}, "deny rules"),
    ({"deny": None}, "settings could not be read"),
])
def test_blocked_redirects_stay_on_claude(launch_args, fragment):
    decision = run("implement", **launch_args)
    assert (decision.action, decision.target, decision.worker) == (
        "set_model", "sonnet", None,
    )
    assert fragment in decision.reason


def test_read_only_agents_never_get_the_write_worker():
    assert needs_write("implement", "general-purpose", CONFIG) is True
    assert needs_write("implement", "Explore", CONFIG) is False
    assert needs_write("review", "general-purpose", CONFIG) is False
    decision = run("implement", agent_type="Explore", mode="default")
    assert (decision.action, decision.worker) == ("redirect_codex", "codex-read")


def test_block_reason_is_none_when_eligible():
    assert redirect_block_reason(launch(), True, CONFIG) is None
    assert redirect_block_reason(launch(mode="default"), False, CONFIG) is None


def test_unknown_quota_is_treated_as_normal_and_noted():
    decision = run("plan", claude=unknown("claude"))
    assert decision.target == "opus"
    assert "claude quota unknown, treated as normal" in decision.reason


def balanced(claude_week, codex_week, **kwargs):
    return run(
        "plan",
        claude=quota("claude", week=claude_week, week_elapsed=0.5),
        codex=quota("codex", week=codex_week, week_elapsed=0.5),
        **kwargs
    )


def test_balance_promotes_the_underused_plan():
    decision = balanced(45, 10)  # weekly projections: 90% and 20%
    assert (decision.action, decision.target) == ("redirect_codex", "codex-deep")
    assert "balance" in decision.reason


@pytest.mark.parametrize("claude_week,codex_week", [(42, 10), (45, 30), (45, 35)])
def test_balance_needs_a_real_imbalance(claude_week, codex_week):
    assert balanced(claude_week, codex_week).target == "opus"


def test_balance_needs_both_projections():
    decision = run(
        "plan",
        claude=quota("claude", week=45, week_elapsed=0.5),
        codex=quota("codex", week=1),
    )
    assert decision.target == "opus"


def test_balance_never_picks_a_blocked_candidate():
    assert balanced(45, 10, mode="plan").target == "opus"


def test_override_skips_balance():
    assert balanced(45, 10, override="opus").target == "opus"
