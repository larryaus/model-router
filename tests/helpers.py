"""Builders shared by the test suite. Later tasks append to this file."""

NOW = 1_800_000_000


import json
import os

from model_router.quota.claude import write_snapshot


def _reset(now, minutes, elapsed=0.1):
    """A reset time that leaves the window `elapsed` of the way through."""
    return now + int(round(minutes * 60 * (1 - elapsed)))


def seed_claude(paths, five, week, now=NOW):
    write_snapshot(
        {
            "rate_limits": {
                "five_hour": {"used_percentage": five, "resets_at": _reset(now, 300)},
                "seven_day": {"used_percentage": week, "resets_at": _reset(now, 10080)},
            }
        },
        paths.claude_quota,
        now,
    )


def codex_line(primary, secondary, timestamp="2026-10-07T01:28:13.535Z"):
    return json.dumps({
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": None,
            "rate_limits": {"primary": primary, "secondary": secondary},
        },
    })


def write_rollout(sessions_dir, name, lines, mtime):
    folder = sessions_dir / "2026" / "10" / "07"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("rollout-%s.jsonl" % name)
    path.write_text("".join(line + "\n" for line in lines))
    os.utime(str(path), (mtime, mtime))
    return path


def seed_codex(paths, five, week, now=NOW):
    write_rollout(
        paths.codex_sessions,
        "seed",
        [codex_line(
            {"used_percent": five, "window_minutes": 300, "resets_at": _reset(now, 300)},
            {"used_percent": week, "window_minutes": 10080, "resets_at": _reset(now, 10080)},
        )],
        mtime=now,
    )


from model_router.config import build_config


def default_config(**overrides):
    """The built-in defaults, with top-level keys overridden."""
    config, errors = build_config(overrides)
    assert config is not None, errors
    return config


from model_router.quota.types import PlanQuota, Window


def window(used, minutes=300, elapsed=0.1):
    return Window(float(used), minutes, _reset(NOW, minutes, elapsed))


def quota(plan, five=0, week=0, five_elapsed=0.1, week_elapsed=0.1):
    return PlanQuota(
        plan,
        [window(five, 300, five_elapsed), window(week, 10080, week_elapsed)],
        NOW,
        True,
    )
