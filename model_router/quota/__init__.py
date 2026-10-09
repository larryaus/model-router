"""Quota readers. `read_quotas` is the one call the rest of the router uses."""

from typing import Dict

from model_router.paths import Paths
from model_router.quota.claude import read_claude_quota
from model_router.quota.codex import read_codex_quota
from model_router.quota.types import PlanQuota


def read_quotas(paths: Paths) -> Dict[str, PlanQuota]:
    return {
        "claude": read_claude_quota(paths.claude_quota),
        "codex": read_codex_quota(paths.codex_sessions),
    }
