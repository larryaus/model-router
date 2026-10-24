"""Judge a finished launch: pass, retry, escalate, or hand it to a person.

The launch-time counterpart is `policy.decide`. Like it, this is pure: the
verify result, the quota, and the current time are passed in.
"""

from functools import partial
from typing import Dict, List, NamedTuple, Optional, Sequence

from model_router.config import WEIGHTS, Config
from model_router.policy import (
    Launch,
    needs_write,
    plan_levels,
    redirect_block_reason,
    rejection,
)
from model_router.quota.types import PlanQuota


class Outcome(NamedTuple):
    action: str
    target: Optional[str]
    reason: str
    levels: Dict[str, str]


def _with_notes(reason: str, notes: List[str]) -> str:
    return "%s (%s)" % (reason, "; ".join(notes)) if notes else reason


def judge(
    category: str,
    launch: Launch,
    attempts: Sequence[str],
    verify: str,
    quotas: Dict[str, PlanQuota],
    config: Config,
    now: int,
    retry_same: int,
) -> Outcome:
    """What to do once a launch has finished and its result was verified.

    `launch` is the task's original launch, as `decide` saw it. `attempts`
    names the target of each launch of this task so far, oldest first; the
    last one just ran. `verify` is "pass" or "fail", and anything else means
    the result is unknown. `retry_same` is how many extra tries one target
    gets before the task moves to a heavier one.
    """
    levels = plan_levels(quotas, now, config)
    # Without a known target there is nothing to retry or escalate from.
    if not attempts or attempts[-1] not in config.targets:
        return Outcome("keep", None, "attempt history unknown", levels)
    last = config.targets[attempts[-1]]
    if verify == "pass":
        return Outcome("pass", None, "verify passed on %s" % last.name, levels)
    if verify != "fail":
        return Outcome("keep", None, "verify result unknown", levels)

    block = redirect_block_reason(
        launch, needs_write(category, launch.agent_type, config), config
    )
    # The next launch carries a route tag, and a tag skips the conserve and
    # critical limits. The weight rule is applied here instead, so a retry
    # or an escalation never spends what `decide` would not have.
    reject = partial(rejection, levels=levels, block=block)
    notes: List[str] = []

    if attempts.count(last.name) <= retry_same:
        why = reject(last)
        if why is None:
            return Outcome(
                "retry", last.name, "verify failed on %s; retry it" % last.name, levels,
            )
        notes.append("no retry: %s" % why)

    # Heavier targets only, the category's preferences first, then the rest
    # in config order. A target that already failed this task is not reused.
    floor = WEIGHTS.index(last.weight)
    preferred = config.preferences.get(category, [])
    order = list(preferred) + [name for name in config.targets if name not in preferred]
    for name in order:
        target = config.targets[name]
        if WEIGHTS.index(target.weight) <= floor or name in attempts:
            continue
        why = reject(target)
        if why is not None:
            notes.append("%s skipped: %s" % (name, why))
            continue
        reason = "verify failed on %s; escalate to %s" % (last.name, name)
        return Outcome("escalate", name, _with_notes(reason, notes), levels)

    reason = "verify failed on %s; no heavier target is allowed" % last.name
    return Outcome("human", None, _with_notes(reason, notes), levels)
