import json
import subprocess

from model_router.hooks.statusline import run
from model_router.quota.claude import read_claude_quota
from tests.helpers import NOW

STATUS_INPUT = json.dumps({
    "session_id": "s",
    "rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": NOW + 1000}},
})


class FakeRunner:
    def __init__(self, stdout="relayed", error=None):
        self.stdout = stdout
        self.error = error
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(command, 0, stdout=self.stdout, stderr="")


def with_passthrough(paths, command="my-statusline --flag"):
    paths.config.write_text(json.dumps({"statusline": {"passthrough": command}}))


def test_captures_quota_and_prints_nothing_without_a_passthrough(paths):
    runner = FakeRunner()
    assert run(STATUS_INPUT, paths, NOW, runner) == ""
    assert read_claude_quota(paths.claude_quota).windows[0].used_pct == 12.0
    assert runner.calls == []


def test_relays_stdin_to_the_passthrough_and_prints_its_output(paths):
    with_passthrough(paths)
    runner = FakeRunner(stdout="branch main\n")
    assert run(STATUS_INPUT, paths, NOW, runner) == "branch main\n"
    command, kwargs = runner.calls[0]
    assert command == "my-statusline --flag"
    assert kwargs["input"] == STATUS_INPUT
    assert kwargs["shell"] is True and kwargs["timeout"] == 2


def test_a_passthrough_timeout_prints_nothing_but_still_captures(paths):
    with_passthrough(paths)
    runner = FakeRunner(error=subprocess.TimeoutExpired("my-statusline", 2))
    assert run(STATUS_INPUT, paths, NOW, runner) == ""
    assert read_claude_quota(paths.claude_quota).known is True


def test_garbage_input_is_still_relayed(paths):
    with_passthrough(paths)
    assert run("not json", paths, NOW, FakeRunner()) == "relayed"
    assert not paths.claude_quota.exists()


def test_an_unwritable_state_directory_still_relays(paths, tmp_path):
    with_passthrough(paths)
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    broken = paths._replace(state_dir=blocker / "state")
    assert run(STATUS_INPUT, broken, NOW, FakeRunner()) == "relayed"


def test_a_config_syntax_error_does_not_blank_the_status_line(paths):
    with_passthrough(paths)
    run(STATUS_INPUT, paths, NOW, FakeRunner())
    paths.config.write_text('{"statusline": {"passthrough": "my-statusline --flag"},}')
    runner = FakeRunner(stdout="still here")
    assert run(STATUS_INPUT, paths, NOW, runner) == "still here"
    assert runner.calls[0][0] == "my-statusline --flag"


def test_removing_the_config_stops_the_passthrough(paths):
    with_passthrough(paths)
    run(STATUS_INPUT, paths, NOW, FakeRunner())
    paths.config.unlink()
    runner = FakeRunner()
    assert run(STATUS_INPUT, paths, NOW, runner) == ""
    assert runner.calls == []


def test_a_removed_passthrough_stays_removed_through_a_later_syntax_error(paths):
    with_passthrough(paths)
    run(STATUS_INPUT, paths, NOW, FakeRunner())
    paths.config.write_text("{}")
    run(STATUS_INPUT, paths, NOW, FakeRunner())
    paths.config.write_text("{not json")
    runner = FakeRunner()
    assert run(STATUS_INPUT, paths, NOW, runner) == ""
    assert runner.calls == []


def test_the_passthrough_really_runs(paths):
    with_passthrough(paths, "cat")
    assert run(STATUS_INPUT, paths, NOW) == STATUS_INPUT
