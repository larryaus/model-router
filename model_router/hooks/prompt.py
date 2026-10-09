"""UserPromptSubmit adapter: tell the main agent when a plan's level changes."""

from typing import Any, Dict, List, Mapping, Optional

from model_router.config import PLANS, Config, load_config
from model_router.paths import Paths
from model_router.policy import plan_level, window_level
from model_router.quota import read_quotas
from model_router.quota.types import (
    PlanQuota,
    effective_used_pct,
    format_time,
    window_label,
)
from model_router.state import load_state, save_state, session_entry

PLAN_LABELS = {"claude": "Claude", "codex": "Codex"}
LEVEL_TEXT = {
    "normal": "{p} is back to normal.",
    "conserve": "{p} is in conserve: heavy {p} targets are paused.",
    "critical": "{p} is critical: only light {p} targets are used.",
    "exhausted": "{p} is exhausted: no {p} targets are used until it resets.",
}
# The router cannot switch the main conversation's model, so it suggests.
CLAUDE_SUGGESTION = {
    "conserve": "Consider /model sonnet.",
    "critical": "Consider /model haiku, or continue this work in Codex.",
    "exhausted": "Consider /model haiku, or continue this work in Codex.",
}


def _context(text: str) -> Dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        }
    }


def _plan_summary(plan: str, quota: PlanQuota, now: int, config: Config) -> str:
    label = PLAN_LABELS[plan]
    if not quota.known:
        return "%s quota unknown" % label
    parts: List[str] = []
    for window in quota.windows:
        part = "%s %.0f%%" % (window_label(window), effective_used_pct(window, now))
        if window.resets_at and window_level(window, now, config) != "normal":
            part += " (resets %s)" % format_time(window.resets_at)
        parts.append(part)
    return "%s %s" % (label, ", ".join(parts))


def guidance_line(
    quotas: Dict[str, PlanQuota],
    levels: Dict[str, str],
    changed: List[str],
    now: int,
    config: Config,
) -> str:
    summary = "; ".join(_plan_summary(p, quotas[p], now, config) for p in PLANS)
    sentences = [LEVEL_TEXT[levels[p]].format(p=PLAN_LABELS[p]) for p in changed]
    if "claude" in changed and levels["claude"] in CLAUDE_SUGGESTION:
        sentences.append(CLAUDE_SUGGESTION[levels["claude"]])
    return "Model router: %s. %s" % (summary, " ".join(sentences))


def handle(
    payload: Any, paths: Paths, now: int, env: Mapping[str, str]
) -> Optional[Dict[str, Any]]:
    if env.get("MODEL_ROUTER") == "off" or not isinstance(payload, dict):
        return None
    session_id = payload.get("session_id")
    session_id = session_id if isinstance(session_id, str) else ""
    result = load_config(paths.config)

    if result.config is None:
        state = load_state(paths.state)
        entry = session_entry(state, session_id, now)
        if entry.get("config_error_announced"):
            return None
        entry["config_error_announced"] = True
        save_state(paths.state, state, now)
        first = result.errors[0] if result.errors else "unknown error"
        return _context(
            "Model router: the config file is invalid, so routing is off and "
            "launches are unchanged. First error: %s. Run `router status` for "
            "the full list." % first
        )

    config = result.config
    if config.mode != "enforce":
        return None

    quotas = read_quotas(paths)
    levels = {plan: plan_level(quotas[plan], now, config) for plan in PLANS}
    state = load_state(paths.state)
    entry = session_entry(state, session_id, now)
    announced = entry.get("announced")
    if not isinstance(announced, dict):
        announced = {}
    changed = [p for p in PLANS if levels[p] != announced.get(p, "normal")]
    if not changed:
        return None
    entry["announced"] = levels
    save_state(paths.state, state, now)
    return _context(guidance_line(quotas, levels, changed, now, config))
