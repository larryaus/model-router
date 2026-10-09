"""PreToolUse adapter for the Agent tool: route a subagent launch."""

import os
from typing import Any, Dict, List, Mapping, Optional

from model_router import grants
from model_router.classify import classify
from model_router.config import Config, load_config
from model_router.decision_log import append_record
from model_router.hooks.permissions import has_file_deny_rules, settings_files
from model_router.paths import Paths
from model_router.policy import Decision, Launch, decide
from model_router.quota import read_quotas
from model_router.state import load_state, save_state, session_entry

WORKER_PREFIX = "model-router:"
REASON_PREFIX = "Model router: "
DEFAULT_AGENT_TYPE = "general-purpose"
# Stands in for an agent type that is not a usable string. It is in no
# config list, so it can receive Claude model changes but never a redirect.
INVALID_AGENT_TYPE = "<invalid>"


def _output(fields: Dict[str, Any]) -> Dict[str, Any]:
    fields = dict(fields, hookEventName="PreToolUse")
    return {"hookSpecificOutput": fields}


def build_response(
    decision: Decision,
    tool_input: Dict[str, Any],
    config: Config,
    paths: Paths,
    now: int,
    cwd: Any,
) -> Optional[Dict[str, Any]]:
    if decision.action == "keep":
        return None
    reason = REASON_PREFIX + decision.reason
    if decision.action == "deny":
        return _output({
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        })
    target = config.targets[decision.target]
    # updatedInput replaces the whole input, so start from a full copy.
    updated = dict(tool_input)
    if decision.action == "set_model":
        if tool_input.get("model") == target.model:
            return None
        updated["model"] = target.model
    else:
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            # Eligibility already requires a usable cwd; never redirect without one.
            return None
        prompt = tool_input.get("prompt")
        # The grant is what lets the worker start Codex, and it fixes the
        # sandbox, effort, model, and directory. The prompt only carries the
        # nonce that redeems it.
        nonce = grants.issue(
            paths.state_dir,
            "write" if decision.worker == "codex-write" else "read",
            target.effort,
            target.model,
            cwd,
            now,
        )
        updated["subagent_type"] = WORKER_PREFIX + decision.worker
        updated.pop("model", None)
        updated["prompt"] = "ROUTER_GRANT: %s\n%s" % (
            nonce, prompt if isinstance(prompt, str) else "",
        )
    return _output({
        "permissionDecision": "allow",
        "permissionDecisionReason": reason,
        "updatedInput": updated,
    })


def _log_invalid_once(
    paths: Paths, session_id: str, errors: List[str], now: int
) -> None:
    state = load_state(paths.state)
    entry = session_entry(state, session_id, now)
    if entry.get("config_invalid_logged"):
        return
    entry["config_invalid_logged"] = True
    save_state(paths.state, state, now)
    append_record(paths.log, {
        "ts": int(now),
        "event": "config_invalid",
        "session_id": session_id,
        "errors": errors,
    })


def handle(
    payload: Any, paths: Paths, now: int, env: Mapping[str, str]
) -> Optional[Dict[str, Any]]:
    if env.get("MODEL_ROUTER") == "off" or not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != "Agent":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    session_id = payload.get("session_id")
    session_id = session_id if isinstance(session_id, str) else ""

    result = load_config(paths.config)
    if result.config is None:
        # An invalid config must never turn enforcement on. Go inert.
        _log_invalid_once(paths, session_id, result.errors, now)
        return None
    config = result.config
    if config.mode == "off":
        return None

    # Anything unknown must block a redirect, never pass by default. Only a
    # launch that names no agent type at all is the general-purpose agent.
    agent_type = tool_input.get("subagent_type", DEFAULT_AGENT_TYPE)
    if not isinstance(agent_type, str) or not agent_type:
        agent_type = INVALID_AGENT_TYPE
    permission_mode = payload.get("permission_mode")
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and os.path.isabs(cwd):
        project_dir = env.get("CLAUDE_PROJECT_DIR") or ""
        file_deny_rules = has_file_deny_rules(settings_files(
            cwd,
            paths.claude_home,
            paths.managed_settings,
            project_dir if os.path.isabs(project_dir) else "",
        ))
    else:
        # Without the session's directory the project settings cannot be
        # found, so whether they hold file rules is unknown.
        file_deny_rules = None
    launch = Launch(
        agent_type=agent_type,
        permission_mode=permission_mode if isinstance(permission_mode, str) else None,
        nested=payload.get("agent_id") is not None,
        file_deny_rules=file_deny_rules,
    )
    classification = classify(tool_input, config)
    decision = decide(classification, launch, read_quotas(paths), config, now)

    # No prompt text and no description: only what is needed to audit routing.
    append_record(paths.log, {
        "ts": int(now),
        "session_id": session_id,
        "agent_type": agent_type,
        "category": classification.category,
        "classified_by": classification.source,
        "action": decision.action,
        "target": decision.target,
        "worker": decision.worker,
        "reason": decision.reason,
        "levels": decision.levels,
        "mode": config.mode,
        "permission_mode": launch.permission_mode,
        "nested": launch.nested,
        "deny_rules": launch.file_deny_rules,
    })
    if config.mode != "enforce":
        return None
    return build_response(decision, tool_input, config, paths, now, cwd)
