"""The routing policy. Pure: no I/O, and the current time is passed in."""

from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from model_router.classify import Classification
from model_router.config import PLANS, WEIGHTS, Config, Target
from model_router.quota.types import (
    WEEK_MINUTES,
    PlanQuota,
    Window,
    effective_used_pct,
    format_time,
    unknown,
)

LEVEL_ORDER = ("normal", "conserve", "critical", "exhausted")
ALLOWED_WEIGHTS = {
    "normal": ("heavy", "medium", "light"),
    "conserve": ("medium", "light"),
    "critical": ("light",),
    "exhausted": (),
}
# Never altered, whatever the config says. Covers the router's own workers
# (with and without the plugin namespace), which prevents redirect loops, and
# forks, which always inherit the parent's model.
ALWAYS_PASSTHROUGH = (
    "fork",
    "codex-read",
    "codex-write",
    "model-router:codex-read",
    "model-router:codex-write",
)


class Launch(NamedTuple):
    agent_type: str
    permission_mode: Optional[str]
    nested: bool
    file_deny_rules: Optional[bool]


class Decision(NamedTuple):
    action: str
    target: Optional[str]
    reason: str
    levels: Dict[str, str]
    worker: Optional[str]


def projected_pct(window: Window, now: int, warmup: float) -> Optional[float]:
    """Usage at the end of the window if the current pace holds.

    Undefined when the window has no usable reset time, has not started, or
    is still inside the warmup, where early readings are too noisy.
    """
    if window.resets_at is None or window.resets_at <= now:
        return None
    if window.window_minutes <= 0:
        return None
    seconds = window.window_minutes * 60.0
    elapsed = max(0.0, min(1.0, 1.0 - (window.resets_at - now) / seconds))
    if elapsed <= 0.0 or elapsed < warmup:
        return None
    return window.used_pct / elapsed


def window_level(window: Window, now: int, config: Config) -> str:
    used = effective_used_pct(window, now)
    if used >= config.exhausted:
        return "exhausted"
    if used >= config.critical:
        return "critical"
    if used >= config.conserve:
        return "conserve"
    projected = projected_pct(window, now, config.warmup)
    if projected is not None and projected >= 100.0:
        return "conserve"
    return "normal"


def plan_level(quota: PlanQuota, now: int, config: Config) -> str:
    level = "normal"
    for window in quota.windows:
        candidate = window_level(window, now, config)
        if LEVEL_ORDER.index(candidate) > LEVEL_ORDER.index(level):
            level = candidate
    return level


def weekly_projection(quota: PlanQuota, now: int, config: Config) -> Optional[float]:
    for window in quota.windows:
        if window.window_minutes == WEEK_MINUTES:
            return projected_pct(window, now, config.warmup)
    return None


def needs_write(category: str, agent_type: str, config: Config) -> bool:
    """Whether a redirect of this launch would need the write-enabled worker."""
    return (
        category in config.write_categories
        and agent_type not in config.readonly_agents
    )


def redirect_block_reason(launch: Launch, write: bool, config: Config) -> Optional[str]:
    """Why this launch may not be sent to Codex, or None if it may.

    A redirect leaves Claude Code's permission system for Codex's sandbox, so
    it is allowed only when everything the hook can see says the sandbox's
    file access is no wider than the session's. Anything unknown blocks.
    """
    if launch.agent_type not in config.redirectable:
        return "agent type %s is not redirectable" % launch.agent_type
    if launch.nested:
        return "nested launch"
    kind = "write" if write else "read"
    allowed = config.write_redirect_modes if write else config.read_redirect_modes
    if launch.permission_mode not in allowed:
        return "permission mode %s does not allow a %s redirect" % (
            launch.permission_mode or "unknown", kind,
        )
    if launch.file_deny_rules is None:
        return "settings could not be read"
    if launch.file_deny_rules:
        return "file-scoped deny rules are present"
    return None


def _balance(
    later_names: Sequence[str],
    chosen: Target,
    rejection: Callable[[Target], Optional[str]],
    quotas: Dict[str, PlanQuota],
    config: Config,
    now: int,
) -> Optional[Tuple[Target, str]]:
    """Promote a later list entry when the chosen plan is heading over."""
    own = weekly_projection(quotas[chosen.plan], now, config)
    if own is None or own <= config.overused:
        return None
    for name in later_names:
        candidate = config.targets[name]
        if candidate.plan == chosen.plan or rejection(candidate) is not None:
            continue
        other = weekly_projection(quotas[candidate.plan], now, config)
        if other is not None and other < config.underused:
            note = "balance: %s weekly projected %.0f%%, %s %.0f%%; promoted over %s" % (
                chosen.plan, own, candidate.plan, other, chosen.name,
            )
            return candidate, note
    return None


def _earliest_reset(
    quotas: Dict[str, PlanQuota], levels: Dict[str, str], now: int
) -> Optional[int]:
    resets = [
        window.resets_at
        for plan in PLANS
        if levels[plan] != "normal"
        for window in quotas[plan].windows
        if window.resets_at is not None and window.resets_at > now
    ]
    return min(resets) if resets else None


def decide(
    classification: Classification,
    launch: Launch,
    quotas: Dict[str, PlanQuota],
    config: Config,
    now: int,
) -> Decision:
    quotas = {plan: quotas.get(plan) or unknown(plan) for plan in PLANS}
    levels = {plan: plan_level(quotas[plan], now, config) for plan in PLANS}
    category = classification.category

    if launch.agent_type in ALWAYS_PASSTHROUGH or launch.agent_type in config.passthrough:
        return Decision(
            "keep", None, "%s is a passthrough agent type" % launch.agent_type,
            levels, None,
        )
    if classification.override == "keep":
        return Decision("keep", None, "kept by [route:keep]", levels, None)

    write = needs_write(category, launch.agent_type, config)
    block = redirect_block_reason(launch, write, config)
    notes: List[str] = [
        "%s quota unknown, treated as normal" % plan
        for plan in PLANS
        if not quotas[plan].known
    ]

    def rejection(target: Target, soft_levels: bool = True) -> Optional[str]:
        level = levels[target.plan]
        if soft_levels:
            if target.weight not in ALLOWED_WEIGHTS[level]:
                return "%s %s" % (target.plan, level)
        elif level == "exhausted":
            return "%s exhausted" % target.plan
        if target.plan == "codex" and block is not None:
            return block
        return None

    chosen: Optional[Target] = None
    override = classification.override
    if override is not None:
        target = config.targets.get(override)
        if target is None:
            notes.append("unknown route tag %r ignored" % override)
        else:
            # An explicit override ignores conserve and critical, but not
            # exhausted and not redirect eligibility.
            why = rejection(target, soft_levels=False)
            if why is None:
                chosen = target
                notes.append("override")
            else:
                notes.append("override %s rejected: %s" % (target.name, why))

    if chosen is None:
        names = config.preferences[category]
        for index, name in enumerate(names):
            target = config.targets[name]
            why = rejection(target)
            if why is not None:
                notes.append("%s skipped: %s" % (name, why))
                continue
            chosen = target
            promoted = _balance(names[index + 1:], target, rejection, quotas, config, now)
            if promoted is not None:
                chosen = promoted[0]
                notes.append(promoted[1])
            break

    if chosen is None:
        allowed = [t for t in config.targets.values() if rejection(t) is None]
        if allowed:
            # min() keeps the first of equals, so ties follow config order.
            chosen = min(allowed, key=lambda t: WEIGHTS.index(t.weight))
            notes.append("fallback to the lightest allowed target")

    if chosen is None:
        reason = "%s -> denied: no target is allowed (%s)" % (category, "; ".join(notes))
        reset = _earliest_reset(quotas, levels, now)
        if reset is not None:
            reason += "; earliest reset %s" % format_time(reset)
        return Decision("deny", None, reason, levels, None)

    reason = "%s -> %s" % (category, chosen.name)
    if notes:
        reason += " (%s)" % "; ".join(notes)
    if chosen.plan == "codex":
        worker = "codex-write" if write else "codex-read"
        return Decision("redirect_codex", chosen.name, reason, levels, worker)
    return Decision("set_model", chosen.name, reason, levels, None)
