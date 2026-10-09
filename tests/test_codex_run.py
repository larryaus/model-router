import subprocess
from pathlib import Path

from model_router.codex_run import build_command, run, split_effort

ROOT = Path(__file__).resolve().parents[1]


class FakeCodex:
    def __init__(self, message, returncode=0, stderr="", error=None):
        self.message = message
        self.returncode = returncode
        self.stderr = stderr
        self.error = error
        self.command = None
        self.kwargs = None

    def __call__(self, command, **kwargs):
        self.command, self.kwargs = command, kwargs
        if self.error is not None:
            raise self.error
        if self.message is not None:
            out_file = command[command.index("--output-last-message") + 1]
            Path(out_file).write_text(self.message)
        return subprocess.CompletedProcess(
            command, self.returncode, stdout="", stderr=self.stderr
        )


def prompt_file(tmp_path, text):
    path = tmp_path / "task.md"
    path.write_text(text)
    return str(path)


def test_split_effort():
    assert split_effort("ROUTER_EFFORT: xhigh\nDo it\nnow") == ("xhigh", "Do it\nnow")
    assert split_effort("Do it") == ("medium", "Do it")
    assert split_effort('ROUTER_EFFORT: xhigh" --sandbox danger\nDo it') == (
        "medium", "Do it",
    )


def test_read_command():
    assert build_command("read", "high", "/work", "/tmp/out.txt") == [
        "codex", "exec",
        "--sandbox", "read-only",
        "--cd", "/work",
        "--skip-git-repo-check",
        "-c", 'model_reasoning_effort="high"',
        "--output-last-message", "/tmp/out.txt",
        "-",
    ]


def test_write_command_uses_the_workspace_sandbox():
    command = build_command("write", "xhigh", "/work", "/tmp/out.txt")
    assert command[command.index("--sandbox") + 1] == "workspace-write"


def test_no_command_widens_the_sandbox():
    for mode in ("read", "write"):
        command = build_command(mode, "xhigh", "/work", "/tmp/out.txt")
        assert "danger-full-access" not in command
        assert "--add-dir" not in command
        assert not any(part.startswith("--dangerously") for part in command)


def test_run_returns_the_final_message(tmp_path):
    fake = FakeCodex("All done.\n")
    code, text = run(
        "write", prompt_file(tmp_path, "ROUTER_EFFORT: xhigh\nFix the bug\n"),
        "/work", fake,
    )
    assert (code, text) == (0, "All done.")
    assert fake.kwargs["input"] == "Fix the bug\n"
    assert 'model_reasoning_effort="xhigh"' in fake.command
    assert fake.command[fake.command.index("--cd") + 1] == "/work"


def test_run_reports_a_codex_failure(tmp_path):
    fake = FakeCodex(None, returncode=3, stderr="usage limit reached")
    code, text = run("read", prompt_file(tmp_path, "Look around"), "/work", fake)
    assert code == 3
    assert "Codex failed (exit 3)" in text and "usage limit reached" in text


def test_an_empty_answer_is_a_failure(tmp_path):
    code, text = run(
        "read", prompt_file(tmp_path, "Look around"), "/work", FakeCodex("")
    )
    assert code == 1 and "no answer" in text


def test_bad_mode_missing_file_and_empty_task(tmp_path):
    fake = FakeCodex("unused")
    assert run("admin", prompt_file(tmp_path, "x"), "/work", fake)[0] == 2
    assert run("read", str(tmp_path / "absent.md"), "/work", fake)[0] == 2
    assert run("read", prompt_file(tmp_path, "ROUTER_EFFORT: high\n  \n"), "/work", fake)[0] == 2
    assert fake.command is None


def test_codex_not_installed(tmp_path):
    fake = FakeCodex(None, error=FileNotFoundError("codex"))
    code, text = run("read", prompt_file(tmp_path, "Look around"), "/work", fake)
    assert code == 127 and "could not be started" in text


def test_each_agent_file_pins_its_own_mode():
    read_agent = (ROOT / "agents" / "codex-read.md").read_text()
    write_agent = (ROOT / "agents" / "codex-write.md").read_text()
    assert "codex-run read" in read_agent and "codex-run write" not in read_agent
    assert "codex-run write" in write_agent and "codex-run read" not in write_agent
    for text in (read_agent, write_agent):
        assert "model: haiku" in text
        assert '"${CLAUDE_PLUGIN_ROOT}/bin/router"' in text
