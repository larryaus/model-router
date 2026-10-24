"""PostToolUse adapter for the Agent tool: verify a routed launch once it ends.

The hook runs in the background and only records. After a write task the
router routed, it runs the owner's verify command, asks `outcome.judge` what
should happen next, and logs the answer. It never responds to Claude Code.
"""

import os
from typing import Any, Dict, Mapping, Optional

from model_router.config import load_config
from model_router.decision_log import append_record, read_recent
from model_router.outcome import judge
from model_router.paths import Paths
from model_router.policy import Launch
from model_router.quota import read_quotas

# How many log records to search for the launch's decision. Every session
# writes to the same log, so a long task can be followed by many records.
# A decision rotated out of the log is not found, and nothing is verified.
LOOKBACK_RECORDS = 2000
ROUTED_ACTIONS = ("set_model", "redirect_codex")


def find_decision(paths: Paths, tool_use_id: str) -> Optional[Dict[str, Any]]:
    for record in reversed(read_recent(paths.log, LOOKBACK_RECORDS)):
        if record.get("tool_use_id") == tool_use_id and "action" in record:
            return record
    return None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def handle(payload: Any, paths: Paths, now: int, env: Mapping[str, str]) -> None:
    if env.get("MODEL_ROUTER") == "off" or not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != "Agent":
        return None
    # Launched from inside a subagent. The outer task is verified when it ends.
    if payload.get("agent_id") is not None:
        return None
    response = payload.get("tool_response")
    # A background launch returns at once, before its work is done.
    if not isinstance(response, dict) or response.get("status") != "completed":
        return None
    tool_use_id = payload.get("tool_use_id")
    cwd = payload.get("cwd")
    if not isinstance(tool_use_id, str) or not tool_use_id:
        return None
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return None

    config = load_config(paths.config).config
    if config is None or config.mode == "off" or config.verify_command is None:
        return None
    decision = find_decision(paths, tool_use_id)
    if decision is None or decision.get("action") not in ROUTED_ACTIONS:
        return None
    category = decision.get("category")
    agent_type = decision.get("agent_type")
    if category not in config.verify_categories or agent_type in config.readonly_agents:
        return None

    # Imported here because most launches end with verification off, and
    # subprocess is one of the slower standard-library imports.
    from model_router import verify

    result = verify.run(config.verify_command, cwd, config.verify_timeout)
    # The launch exactly as `decide` saw it, from its own record.
    launch = Launch(
        agent_type,
        decision.get("permission_mode"),
        decision.get("nested") is True,
        decision.get("deny_rules"),
    )
    target = decision.get("target")
    outcome = judge(
        category, launch, [target], result, read_quotas(paths), config, now,
        config.verify_retry_same,
    )
    session_id = payload.get("session_id")
    resolved_model = response.get("resolvedModel")
    # In shadow mode `target` is what the router would have used, and
    # `resolved_model` is what actually ran.
    append_record(paths.log, {
        "ts": int(now),
        "event": "outcome",
        "session_id": session_id if isinstance(session_id, str) else "",
        "tool_use_id": tool_use_id,
        "category": category,
        "target": target,
        "resolved_model": resolved_model if isinstance(resolved_model, str) else None,
        "duration_ms": _number(payload.get("duration_ms")),
        "verify": result,
        "outcome": outcome.action,
        "next_target": outcome.target,
        "reason": outcome.reason,
        "levels": outcome.levels,
        "mode": config.mode,
    })
    return None
