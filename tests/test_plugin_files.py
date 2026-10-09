import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    return json.loads((ROOT / relative).read_text())


def test_plugin_manifest():
    assert load(".claude-plugin/plugin.json")["name"] == "model-router"


def test_marketplace_lists_the_plugin_at_the_repository_root():
    entry = load(".claude-plugin/marketplace.json")["plugins"][0]
    assert (entry["name"], entry["source"]) == ("model-router", "./")


def test_hooks_file_wires_the_pre_launch_hook():
    entry = load("hooks/hooks.json")["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Agent"
    assert entry["hooks"] == [{
        "type": "command",
        "command": '"${CLAUDE_PLUGIN_ROOT}"/bin/router hook pre-agent',
        "timeout": 5,
    }]


def test_entry_script_is_executable():
    assert os.access(str(ROOT / "bin" / "router"), os.X_OK)
