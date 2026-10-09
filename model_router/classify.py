"""Name the category of a subagent launch from cheap, deterministic signals."""

import re
from typing import Any, Dict, NamedTuple, Optional, Tuple

from model_router.config import KEYWORD_CATEGORIES, Config

ROUTE_TAG = re.compile(r"\[route:([A-Za-z0-9_-]+)\]")
PROMPT_SCAN_CHARS = 500


class Classification(NamedTuple):
    category: str
    source: str
    override: Optional[str]


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _has_keyword(text: str, keyword: str) -> bool:
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(keyword) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _category(
    agent_type: str, description: str, prompt: str, config: Config
) -> Tuple[str, str]:
    if agent_type in config.agent_categories:
        return config.agent_categories[agent_type], "agent_type"
    for text in (description, prompt[:PROMPT_SCAN_CHARS]):
        for category in KEYWORD_CATEGORIES:
            if any(_has_keyword(text, word) for word in config.keywords[category]):
                return category, "keyword"
    return "default", "default"


def classify(tool_input: Dict[str, Any], config: Config) -> Classification:
    prompt = _text(tool_input.get("prompt"))
    match = ROUTE_TAG.search(prompt)
    override = match.group(1) if match else None
    category, source = _category(
        _text(tool_input.get("subagent_type")),
        _text(tool_input.get("description")),
        ROUTE_TAG.sub("", prompt),
        config,
    )
    return Classification(category, "tag" if override else source, override)
