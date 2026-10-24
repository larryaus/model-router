import time

from model_router import verify


def test_exit_zero_is_a_pass(tmp_path):
    assert verify.run("true", str(tmp_path), 10) == "pass"


def test_a_nonzero_exit_is_a_fail(tmp_path):
    assert verify.run("exit 1", str(tmp_path), 10) == "fail"


def test_it_runs_in_the_given_directory(tmp_path):
    (tmp_path / "marker").write_text("")
    assert verify.run("test -f marker", str(tmp_path), 10) == "pass"


def test_a_command_the_shell_cannot_run_is_unknown_not_a_fail(tmp_path):
    assert verify.run("no-such-command-model-router", str(tmp_path), 10) == "unknown"


def test_a_missing_directory_is_unknown(tmp_path):
    assert verify.run("true", str(tmp_path / "missing"), 10) == "unknown"


def test_ending_by_signal_is_unknown(tmp_path):
    assert verify.run("kill -9 $$", str(tmp_path), 10) == "unknown"


def test_a_timeout_is_unknown_and_stops_the_commands_children(tmp_path):
    # The child would outlive a kill of the shell alone.
    started = time.monotonic()
    result = verify.run("(sleep 1; touch late) & wait", str(tmp_path), 0.3)
    assert result == "unknown"
    assert time.monotonic() - started < 1
    time.sleep(1.5)
    assert not (tmp_path / "late").exists()
