"""Detect file-scoped deny rules in the Claude Code settings.

Codex cannot honour Claude Code's per-path permission rules. If any exist,
no launch may be redirected to Codex. This module only answers "do any
exist?"; it never interprets the paths in a rule.
"""

import json
from pathlib import Path
from typing import Any, List, Optional, Sequence

FILE_TOOLS = ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Glob", "Grep")
# An "ask" rule is also a limit Codex would not honour: it would act where
# Claude Code would have stopped to ask.
RULE_LISTS = ("deny", "ask")
SETTINGS_NAMES = ("settings.json", "settings.local.json")


def settings_files(
    cwd: str, claude_home: Path, managed_settings: Path, project_dir: str = ""
) -> List[Path]:
    """Every settings file that could apply to the session.

    Project settings live at the project root, which the session's current
    directory may have left, so both starting points are walked upward.
    """
    files: List[Path] = [Path(claude_home) / name for name in SETTINGS_NAMES]
    files.append(Path(managed_settings))
    for start in (cwd, project_dir):
        if not start:
            continue
        directory = Path(start)
        for folder in [directory] + list(directory.parents):
            for name in SETTINGS_NAMES:
                candidate = folder / ".claude" / name
                if candidate not in files:
                    files.append(candidate)
    return files


def _is_file_rule(rule: Any) -> bool:
    if not isinstance(rule, str):
        return False
    return rule.split("(", 1)[0].strip() in FILE_TOOLS


def has_file_deny_rules(files: Sequence[Path]) -> Optional[bool]:
    found = False
    for path in files:
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        permissions = data.get("permissions", {})
        if not isinstance(permissions, dict):
            return None
        for name in RULE_LISTS:
            rules = permissions.get(name, [])
            if not isinstance(rules, list):
                return None
            if any(_is_file_rule(rule) for rule in rules):
                found = True
    return found
