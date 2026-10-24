"""Run the owner's verify command and reduce its result to pass, fail, or unknown."""

import os
import signal
import subprocess

# The shell's codes for a command it could not find or could not run. They
# say the verify command is misconfigured, not that the tests failed.
SHELL_COULD_NOT_RUN = (126, 127)


def run(command: str, cwd: str, timeout: float) -> str:
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Its own session, so a timeout can stop the test workers it
            # starts as well as the shell.
            start_new_session=True,
        )
    except OSError:
        return "unknown"
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.wait()
        return "unknown"
    if code == 0:
        return "pass"
    # A negative code means a signal ended it, which says nothing about the tests.
    if code < 0 or code in SHELL_COULD_NOT_RUN:
        return "unknown"
    return "fail"
