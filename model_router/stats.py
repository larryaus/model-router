"""Summarize the decision log for `router stats`. Pure: records in, text out."""

import time
from statistics import median
from typing import Any, Dict, List, Optional, Sequence

from model_router.config import CATEGORIES, WEIGHTS, Config

ROUTED_ACTIONS = ("set_model", "redirect_codex")
NEXT_STEPS = ("pass", "retry", "escalate", "human", "keep")
SHADOW_MARK = " *"
DAY_SECONDS = 24 * 3600


def _number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def _ran(record: Dict[str, Any]) -> str:
    """What did the work for a verified launch.

    In enforce mode that is the target the router chose. In shadow mode the
    router changed nothing, so it is the model Claude Code reports.
    """
    if record.get("mode") == "enforce":
        return str(record.get("target") or "unknown")
    model = record.get("resolved_model")
    return (model if isinstance(model, str) else "unknown") + SHADOW_MARK


def _categories(records: List[Dict[str, Any]]) -> List[str]:
    seen = {str(record.get("category")) for record in records}
    known = [category for category in CATEGORIES if category in seen]
    return known + sorted(seen - set(known))


def _percent(part: int, whole: int) -> str:
    return "%.0f%%" % (100.0 * part / whole) if whole else "-"


def _duration(values: List[float]) -> str:
    if not values:
        return "-"
    seconds = int(round(median(values) / 1000.0))
    if seconds < 60:
        return "%ds" % seconds
    return "%dm%02ds" % divmod(seconds, 60)


def _table(
    headers: Sequence[str], rows: List[Sequence[Any]], text_columns: int
) -> List[str]:
    """Rows under headers, the first `text_columns` left-aligned, numbers right."""
    cells = [[str(cell) for cell in row] for row in [headers] + rows]
    widths = [max(len(row[index]) for row in cells) for index in range(len(headers))]
    return [
        "  " + "  ".join(
            cell.ljust(width) if index < text_columns else cell.rjust(width)
            for index, (cell, width) in enumerate(zip(row, widths))
        ).rstrip()
        for row in cells
    ]


def _routing(decisions: List[Dict[str, Any]], config: Config) -> List[str]:
    rows = []
    for category in _categories(decisions):
        group = [r for r in decisions if str(r.get("category")) == category]
        routed = [r for r in group if r.get("action") in ROUTED_ACTIONS]
        weights = [
            config.targets[r["target"]].weight
            for r in routed
            if r.get("target") in config.targets
        ]
        rows.append([
            category,
            len(group),
            len(routed),
            sum(1 for r in group if r.get("action") == "keep"),
            sum(1 for r in group if r.get("action") == "deny"),
        ] + [weights.count(weight) for weight in reversed(WEIGHTS)])
    headers = ["category", "launches", "routed", "kept", "denied"] + list(reversed(WEIGHTS))
    return ["Routing decisions"] + _table(headers, rows, 1)


def _results(results: List[Dict[str, Any]]) -> List[str]:
    rows = []
    for category in _categories(results):
        in_category = [r for r in results if str(r.get("category")) == category]
        for ran in sorted({_ran(r) for r in in_category}):
            group = [r for r in in_category if _ran(r) == ran]
            verdicts = [r.get("verify") for r in group]
            passed, failed = verdicts.count("pass"), verdicts.count("fail")
            durations = [
                value for value in (_number(r.get("duration_ms")) for r in group)
                if value is not None
            ]
            rows.append([
                category, ran, len(group), passed, failed,
                len(group) - passed - failed,
                _percent(passed, passed + failed),
                _duration(durations),
            ])
    headers = [
        "category", "ran", "results", "pass", "fail", "unknown", "pass rate",
        "median time",
    ]
    lines = ["Verified results, by what ran"] + _table(headers, rows, 2)
    if any(row[1].endswith(SHADOW_MARK) for row in rows):
        lines.append(
            "  * shadow mode: the router changed nothing; this is the model that ran."
        )
    lines.append("  The pass rate leaves out unknown results.")
    return lines


def _next_steps(results: List[Dict[str, Any]]) -> List[str]:
    rows = []
    for category in _categories(results):
        steps = [r.get("outcome") for r in results if str(r.get("category")) == category]
        rows.append([category] + [steps.count(step) for step in NEXT_STEPS])
    return (
        ["What the router would do next"]
        + _table(["category"] + list(NEXT_STEPS), rows, 1)
    )


def render_stats(
    records: List[Dict[str, Any]], config: Config, now: int, days: Optional[int] = None
) -> str:
    if days is not None:
        cutoff = now - days * DAY_SECONDS
        records = [r for r in records if (_number(r.get("ts")) or 0) >= cutoff]
    decisions = [r for r in records if "action" in r]
    results = [r for r in records if r.get("event") == "outcome"]
    if not decisions and not results:
        if days is not None:
            return "No decisions logged in the last %d days." % days
        return "No decisions logged yet."

    stamps = [t for t in (_number(r.get("ts")) for r in records) if t is not None]
    if days is not None:
        span = "Last %d days" % days
    elif stamps:
        span = "Since %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(min(stamps)))
    else:
        span = "All records"
    lines = ["%s: %d decisions, %d verified results" % (
        span, len(decisions), len(results),
    )]
    errors = sum(1 for r in records if r.get("event") == "hook_error")
    if errors:
        lines.append("Hook errors: %d (see `router log`)" % errors)
    if decisions:
        lines += [""] + _routing(decisions, config)
    if results:
        lines += [""] + _results(results) + [""] + _next_steps(results)
    else:
        lines += [
            "",
            "No verified results yet. Set verify.command in the config to collect them.",
        ]
    return "\n".join(lines)
