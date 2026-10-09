"""Load and validate the router configuration.

The loader has exactly three outcomes, and never a partial one:

  valid    the file exists and passed validation
  absent   no file; built-in defaults, which are in shadow mode
  invalid  syntax error, failed validation, or an unknown key; the router
           goes inert and alters nothing
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

CATEGORIES = ("plan", "implement", "review", "explore", "debug", "default")
# Also the order in which the classifier tests keyword categories.
KEYWORD_CATEGORIES = ("review", "debug", "plan", "explore", "implement")
MODES = ("enforce", "shadow", "off")
PLANS = ("claude", "codex")
WEIGHTS = ("light", "medium", "heavy")
CLAUDE_MODELS = ("opus", "sonnet", "haiku", "fable")
CODEX_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
# A Codex model name ends up as a command-line argument, so its shape is fixed.
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PERMISSION_MODES = (
    "default", "plan", "acceptEdits", "auto", "dontAsk", "bypassPermissions",
)

DEFAULTS: Dict[str, Any] = {
    "mode": "shadow",
    "targets": {
        "opus": {"plan": "claude", "model": "opus", "weight": "heavy"},
        "sonnet": {"plan": "claude", "model": "sonnet", "weight": "medium"},
        "haiku": {"plan": "claude", "model": "haiku", "weight": "light"},
        "codex-deep": {"plan": "codex", "effort": "xhigh", "weight": "heavy"},
        "codex": {"plan": "codex", "effort": "medium", "weight": "medium"},
    },
    "preferences": {
        "plan": ["opus", "codex-deep", "sonnet"],
        "implement": ["codex-deep", "sonnet", "codex"],
        "review": ["codex-deep", "opus", "sonnet"],
        "explore": ["haiku", "codex", "sonnet"],
        "debug": ["opus", "codex-deep", "sonnet"],
        "default": ["sonnet", "codex"],
    },
    "thresholds": {"conserve": 70, "critical": 90, "exhausted": 98},
    "projection": {"warmup": 0.15},
    "balance": {"overused": 85, "underused": 60},
    "agent_categories": {"Explore": "explore", "explorer": "explore", "Plan": "plan"},
    "keywords": {
        "review": ["review", "audit", "critique"],
        "debug": ["debug", "root cause", "failing", "flaky", "stack trace"],
        "plan": ["plan", "design", "architecture"],
        "explore": ["find", "locate", "search", "where is", "trace how"],
        "implement": ["implement", "add", "build", "refactor", "fix", "write"],
    },
    "passthrough": [
        "codex-review", "design-review", "implementer",
        "statusline-setup", "claude-code-guide",
    ],
    "redirectable": ["general-purpose", "claude", "Explore", "Plan", "explorer"],
    "readonly_agents": ["Explore", "Plan", "explorer"],
    "write_categories": ["implement", "debug", "default"],
    "read_redirect_modes": ["default", "auto", "acceptEdits", "bypassPermissions"],
    "write_redirect_modes": ["acceptEdits", "bypassPermissions"],
    "statusline": {"passthrough": None},
}

_MERGED_KEYS = (
    "targets", "preferences", "thresholds", "projection", "balance",
    "agent_categories", "keywords", "statusline",
)


class Target(NamedTuple):
    name: str
    plan: str
    model: Optional[str]
    effort: Optional[str]
    weight: str


class Config(NamedTuple):
    mode: str
    targets: Dict[str, Target]
    preferences: Dict[str, List[str]]
    conserve: float
    critical: float
    exhausted: float
    warmup: float
    overused: float
    underused: float
    agent_categories: Dict[str, str]
    keywords: Dict[str, List[str]]
    passthrough: List[str]
    redirectable: List[str]
    readonly_agents: List[str]
    write_categories: List[str]
    read_redirect_modes: List[str]
    write_redirect_modes: List[str]
    statusline_passthrough: Optional[str]


class ConfigResult(NamedTuple):
    status: str
    config: Optional[Config]
    errors: List[str]


def strip_comments(text: str) -> str:
    """Remove // and /* */ comments, leaving string contents alone."""
    out: List[str] = []
    index, length = 0, len(text)
    in_string = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end == -1 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _str_list(value: Any, path: str, errors: List[str], allowed=None) -> List[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        errors.append("%s: expected a list of non-empty strings" % path)
        return []
    if allowed is not None:
        for item in value:
            if item not in allowed:
                errors.append("%s: unknown value %r" % (path, item))
    return list(value)


def _fixed_dict(value: Any, path: str, keys, errors: List[str]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        errors.append("%s: expected an object" % path)
        return {}
    for key in value:
        if key not in keys:
            errors.append("%s.%s: unknown key" % (path, key))
    return value


def _merged(raw: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        if key not in raw:
            merged[key] = default
        elif key in _MERGED_KEYS and isinstance(raw[key], dict):
            combined = dict(default)
            combined.update(raw[key])
            merged[key] = combined
        else:
            merged[key] = raw[key]
    return merged


def _targets(value: Any, errors: List[str]) -> Dict[str, Target]:
    targets: Dict[str, Target] = {}
    if not isinstance(value, dict) or not value:
        errors.append("targets: expected a non-empty object")
        return targets
    for name, raw in value.items():
        path = "targets.%s" % name
        spec = _fixed_dict(raw, path, ("plan", "model", "effort", "weight"), errors)
        plan, weight = spec.get("plan"), spec.get("weight")
        model, effort = spec.get("model"), spec.get("effort")
        if plan not in PLANS:
            errors.append("%s.plan: expected one of %s" % (path, ", ".join(PLANS)))
        if weight not in WEIGHTS:
            errors.append("%s.weight: expected one of %s" % (path, ", ".join(WEIGHTS)))
        if plan == "claude" and model not in CLAUDE_MODELS:
            errors.append(
                "%s.model: expected one of %s" % (path, ", ".join(CLAUDE_MODELS))
            )
        if plan == "codex" and effort not in CODEX_EFFORTS:
            errors.append(
                "%s.effort: expected one of %s" % (path, ", ".join(CODEX_EFFORTS))
            )
        # Optional on a Codex target. Routed Codex runs ignore the owner's
        # Codex config, so this is how a specific Codex model is chosen.
        if plan == "codex" and model is not None and not (
            isinstance(model, str) and MODEL_NAME.match(model)
        ):
            errors.append(
                "%s.model: expected a model name made of letters, digits, "
                "dots, dashes, and underscores" % path
            )
        targets[name] = Target(
            name,
            plan,
            model if plan in PLANS else None,
            effort if plan == "codex" else None,
            weight,
        )
    return targets


def _preferences(value: Any, targets, errors: List[str]) -> Dict[str, List[str]]:
    raw = _fixed_dict(value, "preferences", CATEGORIES, errors)
    preferences: Dict[str, List[str]] = {}
    for category in CATEGORIES:
        path = "preferences.%s" % category
        entry = raw.get(category)
        if isinstance(entry, list) and not entry:
            errors.append("%s: must not be empty" % path)
            preferences[category] = []
            continue
        names = _str_list(entry, path, errors)
        for index, name in enumerate(names):
            if name not in targets:
                errors.append("%s[%d]: unknown target %r" % (path, index, name))
        preferences[category] = names
    return preferences


def build_config(raw: Any) -> Tuple[Optional[Config], List[str]]:
    """Validate a parsed config. Returns (config, []) or (None, errors)."""
    if not isinstance(raw, dict):
        return None, ["config: expected a JSON object at the top level"]
    errors: List[str] = []
    for key in raw:
        if key not in DEFAULTS:
            errors.append("%s: unknown key" % key)
    data = _merged(raw)

    mode = data["mode"]
    if mode not in MODES:
        errors.append("mode: expected one of %s" % ", ".join(MODES))

    targets = _targets(data["targets"], errors)
    preferences = _preferences(data["preferences"], targets, errors)

    thresholds = _fixed_dict(
        data["thresholds"], "thresholds", ("conserve", "critical", "exhausted"), errors
    )
    levels = [thresholds.get(key) for key in ("conserve", "critical", "exhausted")]
    if not all(_is_number(value) and 0 < value <= 100 for value in levels):
        errors.append("thresholds: each value must be a number above 0 and at most 100")
    elif not levels[0] < levels[1] < levels[2]:
        errors.append("thresholds: expected conserve < critical < exhausted")

    projection = _fixed_dict(data["projection"], "projection", ("warmup",), errors)
    warmup = projection.get("warmup")
    if not _is_number(warmup) or not 0 <= warmup < 1:
        errors.append("projection.warmup: expected a number from 0 up to but not including 1")

    balance = _fixed_dict(data["balance"], "balance", ("overused", "underused"), errors)
    overused, underused = balance.get("overused"), balance.get("underused")
    if not _is_number(overused) or not _is_number(underused):
        errors.append("balance: overused and underused must be numbers")
    elif not underused < overused:
        errors.append("balance: underused must be below overused")

    agent_categories: Dict[str, str] = {}
    raw_agents = data["agent_categories"]
    if not isinstance(raw_agents, dict):
        errors.append("agent_categories: expected an object")
    else:
        for agent, category in raw_agents.items():
            if category not in CATEGORIES:
                errors.append(
                    "agent_categories.%s: expected one of %s"
                    % (agent, ", ".join(CATEGORIES))
                )
            agent_categories[agent] = category

    raw_keywords = _fixed_dict(data["keywords"], "keywords", KEYWORD_CATEGORIES, errors)
    keywords = {
        category: _str_list(raw_keywords.get(category, []), "keywords.%s" % category, errors)
        for category in KEYWORD_CATEGORIES
    }

    passthrough = _str_list(data["passthrough"], "passthrough", errors)
    redirectable = _str_list(data["redirectable"], "redirectable", errors)
    readonly_agents = _str_list(data["readonly_agents"], "readonly_agents", errors)
    write_categories = _str_list(
        data["write_categories"], "write_categories", errors, allowed=CATEGORIES
    )
    read_modes = _str_list(
        data["read_redirect_modes"], "read_redirect_modes", errors,
        allowed=PERMISSION_MODES,
    )
    write_modes = _str_list(
        data["write_redirect_modes"], "write_redirect_modes", errors,
        allowed=PERMISSION_MODES,
    )
    for name, modes in (
        ("read_redirect_modes", read_modes),
        ("write_redirect_modes", write_modes),
    ):
        if "plan" in modes:
            errors.append("%s: plan may not be listed" % name)
    if not set(write_modes) <= set(read_modes):
        errors.append("write_redirect_modes: must be a subset of read_redirect_modes")

    statusline = _fixed_dict(data["statusline"], "statusline", ("passthrough",), errors)
    passthrough_command = statusline.get("passthrough")
    if passthrough_command is not None and not (
        isinstance(passthrough_command, str) and passthrough_command
    ):
        errors.append("statusline.passthrough: expected null or a non-empty string")

    if errors:
        return None, errors
    return (
        Config(
            mode=mode,
            targets=targets,
            preferences=preferences,
            conserve=levels[0],
            critical=levels[1],
            exhausted=levels[2],
            warmup=warmup,
            overused=overused,
            underused=underused,
            agent_categories=agent_categories,
            keywords=keywords,
            passthrough=passthrough,
            redirectable=redirectable,
            readonly_agents=readonly_agents,
            write_categories=write_categories,
            read_redirect_modes=read_modes,
            write_redirect_modes=write_modes,
            statusline_passthrough=passthrough_command,
        ),
        [],
    )


def _parse_file(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.loads(strip_comments(handle.read()))


def load_config(path: Path) -> ConfigResult:
    try:
        raw = _parse_file(path)
    except FileNotFoundError:
        config, errors = build_config({})
        return ConfigResult("absent", config, errors)
    except OSError as exc:
        return ConfigResult("invalid", None, ["config: could not read %s: %s" % (path, exc)])
    except ValueError as exc:
        return ConfigResult("invalid", None, ["config: not valid JSON: %s" % exc])
    config, errors = build_config(raw)
    if config is None:
        return ConfigResult("invalid", None, errors)
    return ConfigResult("valid", config, [])


def raw_statusline_passthrough(path: Path) -> Optional[str]:
    """Best-effort read of statusline.passthrough, ignoring validation.

    The status line must keep relaying to the owner's previous command even
    while the rest of the config is broken.
    """
    return read_statusline_passthrough(path)[1]


def read_statusline_passthrough(path: Path) -> Tuple[bool, Optional[str]]:
    """Return (readable, command) for statusline.passthrough.

    `readable` is False only when the file exists but cannot be parsed into
    an object, which is what a typo looks like. An absent file is readable:
    it says, deliberately, that there is no passthrough.
    """
    try:
        raw = _parse_file(path)
    except FileNotFoundError:
        return True, None
    except (OSError, ValueError):
        return False, None
    if not isinstance(raw, dict):
        return False, None
    statusline = raw.get("statusline")
    value = statusline.get("passthrough") if isinstance(statusline, dict) else None
    return True, (value if isinstance(value, str) and value else None)
