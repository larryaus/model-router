from model_router.outcome import judge
from model_router.policy import Launch
from tests.helpers import NOW, default_config, quota

CONFIG = default_config()


def launch(agent_type="general-purpose", mode="acceptEdits", nested=False, deny=False):
    return Launch(agent_type, mode, nested, deny)


def run(attempts, verify="fail", category="implement", claude=None, codex=None,
        retry_same=1, **launch_args):
    quotas = {
        "claude": claude or quota("claude"),
        "codex": codex or quota("codex"),
    }
    return judge(
        category, launch(**launch_args), attempts, verify, quotas, CONFIG, NOW, retry_same,
    )


def test_a_pass_ends_the_task():
    outcome = run(["sonnet"], verify="pass")
    assert (outcome.action, outcome.target) == ("pass", None)
    assert outcome.reason == "verify passed on sonnet"
    assert outcome.levels == {"claude": "normal", "codex": "normal"}


def test_an_unknown_verify_result_changes_nothing():
    for verify in ("unknown", "timeout", ""):
        outcome = run(["sonnet"], verify=verify)
        assert (outcome.action, outcome.target) == ("keep", None)


def test_an_unknown_attempt_history_changes_nothing():
    # Empty, or a target the config no longer has.
    for attempts in ([], ["renamed-target"]):
        outcome = run(attempts)
        assert (outcome.action, outcome.target) == ("keep", None)
        assert outcome.reason == "attempt history unknown"


def test_the_first_failure_retries_the_same_target():
    outcome = run(["sonnet"])
    assert (outcome.action, outcome.target) == ("retry", "sonnet")


def test_retry_same_zero_escalates_at_once():
    outcome = run(["sonnet"], retry_same=0)
    assert outcome.action == "escalate"


def test_escalation_follows_the_category_preferences():
    outcome = run(["sonnet", "sonnet"])
    assert (outcome.action, outcome.target) == ("escalate", "codex-deep")
    assert outcome.reason == "verify failed on sonnet; escalate to codex-deep"


def test_escalation_skips_a_heavy_target_on_a_conserving_plan():
    # opus is not in implement's preferences, so it comes from the rest.
    outcome = run(["sonnet", "sonnet"], codex=quota("codex", five=75))
    assert (outcome.action, outcome.target) == ("escalate", "opus")
    assert "codex-deep skipped: codex conserve" in outcome.reason


def test_escalation_skips_a_codex_target_that_cannot_be_redirected():
    outcome = run(["sonnet", "sonnet"], mode="default")
    assert (outcome.action, outcome.target) == ("escalate", "opus")
    assert "codex-deep skipped: permission mode default" in outcome.reason


def test_escalation_never_reuses_a_target_that_failed():
    outcome = run(["opus", "sonnet", "sonnet"], category="debug")
    assert (outcome.action, outcome.target) == ("escalate", "codex-deep")


def test_no_retry_once_the_target_is_past_its_level():
    # A route tag would skip critical, so the retry must not be offered.
    outcome = run(["sonnet"], claude=quota("claude", five=92))
    assert (outcome.action, outcome.target) == ("escalate", "codex-deep")
    assert "no retry: claude critical" in outcome.reason


def test_an_escalated_target_gets_its_own_retry():
    outcome = run(["sonnet", "sonnet", "opus"], codex=quota("codex", five=75))
    assert (outcome.action, outcome.target) == ("retry", "opus")


def test_failing_on_the_heaviest_allowed_target_goes_to_a_person():
    outcome = run(["sonnet", "sonnet", "opus", "opus"], codex=quota("codex", five=75))
    assert (outcome.action, outcome.target) == ("human", None)
    assert outcome.reason == "verify failed on opus; no heavier target is allowed"


def test_a_tagged_target_past_conserve_goes_straight_to_a_person():
    # opus ran because of a tag that skipped conserve; the router adds nothing.
    outcome = run(["opus"], claude=quota("claude", five=75))
    assert (outcome.action, outcome.target) == ("human", None)
    assert "no retry: claude conserve" in outcome.reason
