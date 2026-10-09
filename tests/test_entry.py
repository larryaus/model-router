"""The hook path runs on every prompt and every subagent launch.

Its start-up cost is almost all imports, so these tests pin which modules it
may not load. The 100 ms budget itself is measured by hand, because a timing
assertion would be flaky.
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEAVY = ("argparse", "subprocess", "tempfile")

PROBE = """
import io, sys
sys.path.insert(0, %(root)r)
from model_router import entry
sys.stdin = io.StringIO(%(stdin)r)
code = entry.main(%(argv)r)
loaded = sorted(name for name in %(heavy)r if name in sys.modules)
sys.stderr.write("CODE=%%d HEAVY=%%s" %% (code, ",".join(loaded)))
"""

LAUNCH = json.dumps({
    "session_id": "s1", "cwd": "/nonexistent-project", "permission_mode": "auto",
    "hook_event_name": "PreToolUse", "tool_name": "Agent",
    "tool_input": {"subagent_type": "Explore", "description": "Find it", "prompt": "Find it"},
})
PROMPT = json.dumps({
    "session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "hello",
})


def probe(tmp_path, argv, stdin):
    script = PROBE % {"root": str(ROOT), "stdin": stdin, "argv": argv, "heavy": HEAVY}
    return subprocess.run(
        ["/usr/bin/python3", "-I", "-c", script],
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("argv,stdin", [
    (["hook", "pre-agent"], LAUNCH),
    (["hook", "prompt"], PROMPT),
])
def test_hooks_load_no_heavy_modules(tmp_path, argv, stdin):
    result = probe(tmp_path, argv, stdin)
    assert result.stderr == "CODE=0 HEAVY=", result.stderr


def test_the_pre_launch_hook_still_logs_through_the_entry_point(tmp_path):
    probe(tmp_path, ["hook", "pre-agent"], LAUNCH)
    log = tmp_path / "Library/Caches/model-router/decisions.jsonl"
    assert json.loads(log.read_text().splitlines()[0])["agent_type"] == "Explore"


@pytest.mark.parametrize("argv", [["hook"], ["hook", "nonsense"], ["hook", "pre-agent", "--x"]])
def test_unknown_hooks_exit_zero_silently(tmp_path, argv):
    result = probe(tmp_path, argv, "not json")
    assert result.stdout == "" and result.stderr.startswith("CODE=0 ")


def test_commands_still_reach_the_cli(tmp_path):
    result = probe(tmp_path, ["status"], "")
    assert "Config: absent" in result.stdout and "CODE=0" in result.stderr
