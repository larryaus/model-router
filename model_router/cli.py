"""Command-line entry point: status, explain, and log."""

import argparse
import os
import sys
import time
from typing import List

from model_router import codex_run
from model_router.classify import classify
from model_router.config import PERMISSION_MODES, PLANS, build_config, load_config
from model_router.decision_log import read_recent
from model_router.hooks import run_hook
from model_router.hooks import pre_agent, prompt, statusline
from model_router.hooks.permissions import has_file_deny_rules, settings_files
from model_router.paths import Paths, from_env
from model_router.policy import Launch, decide, plan_level, projected_pct
from model_router.quota import read_quotas
from model_router.quota.types import effective_used_pct, format_time, window_label


def _window_line(window, now: int, config) -> str:
    projected = projected_pct(window, now, config.warmup)
    shown = "%3.0f%%" % projected if projected is not None else "   -"
    resets = (
        format_time(window.resets_at)
        if window.resets_at is not None and window.resets_at > now
        else "-"
    )
    return "  %-7s %3.0f%%  projected %s  resets %s" % (
        window_label(window), effective_used_pct(window, now), shown, resets,
    )


def render_status(paths: Paths, now: int) -> str:
    result = load_config(paths.config)
    lines: List[str] = []
    if result.status == "valid":
        lines.append("Config: valid, mode %s (%s)" % (result.config.mode, paths.config))
    elif result.status == "absent":
        lines.append(
            "Config: absent, built-in defaults in shadow mode (%s)" % paths.config
        )
    else:
        lines.append(
            "Config: INVALID, routing is off and launches are unchanged (%s)"
            % paths.config
        )
        lines.extend("  - %s" % error for error in result.errors)
    # Levels are still worth showing when the config is broken.
    config = result.config or build_config({})[0]
    quotas = read_quotas(paths)
    for plan in PLANS:
        quota = quotas[plan]
        if not quota.known:
            lines.append("%s: quota unknown (treated as normal)" % plan.capitalize())
            continue
        lines.append("%s: %s" % (plan.capitalize(), plan_level(quota, now, config)))
        lines.extend(_window_line(window, now, config) for window in quota.windows)
    return "\n".join(lines)


def render_explain(
    task: str, agent_type: str, permission_mode: str, cwd: str, paths: Paths, now: int
) -> str:
    result = load_config(paths.config)
    if result.config is None:
        return "\n".join(
            ["Config is invalid, so every launch is kept unchanged."]
            + ["  - %s" % error for error in result.errors]
        )
    config = result.config
    tool_input = {"subagent_type": agent_type, "description": task, "prompt": task}
    classification = classify(tool_input, config)
    launch = Launch(
        agent_type,
        permission_mode,
        False,
        has_file_deny_rules(
            settings_files(cwd, paths.claude_home, paths.managed_settings)
        ),
    )
    decision = decide(classification, launch, read_quotas(paths), config, now)
    outcome = decision.action
    if decision.target:
        outcome += " -> %s" % decision.target
    if decision.worker:
        outcome += " [%s]" % decision.worker
    mode = config.mode
    if mode != "enforce":
        mode += " (the decision is logged at most; launch unchanged)"
    return "\n".join([
        "Category: %s (from %s)" % (classification.category, classification.source),
        "Levels: " + ", ".join("%s %s" % (p, decision.levels[p]) for p in PLANS),
        "Decision: %s" % outcome,
        "Reason: %s" % decision.reason,
        "Mode: %s" % mode,
    ])


def render_log(paths: Paths, count: int) -> str:
    records = read_recent(paths.log, count)
    if not records:
        return "No decisions logged yet."
    lines: List[str] = []
    for record in records:
        timestamp = record.get("ts")
        when = format_time(timestamp) if isinstance(timestamp, (int, float)) else "?"
        if "action" in record:
            lines.append("%s  %-14s %-11s %s" % (
                when, record["action"], record.get("target") or "-",
                record.get("reason", ""),
            ))
        else:
            detail = record.get("error") or "; ".join(record.get("errors", []))
            lines.append("%s  %-14s %s" % (when, record.get("event", "?"), detail))
    return "\n".join(lines)


_HOOK_HANDLERS = {
    "pre-agent": ("pre_agent", pre_agent.handle),
    "prompt": ("prompt", prompt.handle),
}


def _run_hook_command(argv: List[str]) -> int:
    """Run a hook. Always returns 0: exit code 2 would block the launch."""
    try:
        entry = _HOOK_HANDLERS.get(argv[0] if argv else "")
        if entry is None:
            return 0
        text = run_hook(
            entry[0], entry[1], sys.stdin.read(),
            from_env(os.environ), int(time.time()), os.environ,
        )
        if text:
            sys.stdout.write(text + "\n")
    except Exception:
        pass
    return 0


def _run_statusline() -> int:
    try:
        sys.stdout.write(
            statusline.run(sys.stdin.read(), from_env(os.environ), int(time.time()))
        )
    except Exception:
        pass
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="router", description="Model router for Claude Code."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show quota, levels, and config state")
    explain = commands.add_parser("explain", help="dry-run a routing decision")
    explain.add_argument("task", help="the task text, as you would describe it")
    explain.add_argument("--agent", default="general-purpose")
    explain.add_argument(
        "--permission-mode", default="default", choices=PERMISSION_MODES
    )
    log = commands.add_parser("log", help="show recent decisions")
    log.add_argument("-n", type=int, default=20)
    worker = commands.add_parser("codex-run", help="run one task through Codex")
    worker.add_argument("mode", choices=("read", "write"))
    worker.add_argument("prompt_file")
    return parser


def main(argv: List[str]) -> int:
    if argv[:1] == ["hook"]:
        return _run_hook_command(argv[1:])
    if argv[:1] == ["statusline"]:
        return _run_statusline()
    args = _parser().parse_args(argv)
    paths = from_env(os.environ)
    now = int(time.time())
    if args.command == "status":
        print(render_status(paths, now))
    elif args.command == "explain":
        print(render_explain(
            args.task, args.agent, args.permission_mode, os.getcwd(), paths, now
        ))
    elif args.command == "log":
        print(render_log(paths, args.n))
    elif args.command == "codex-run":
        code, text = codex_run.run(args.mode, args.prompt_file, os.getcwd())
        print(text)
        return code
    return 0
