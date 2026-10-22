"""Run a tool with a timeout that also stops wrapper-launched child processes."""
import os
import signal
import subprocess


def run_with_timeout(argv, *, timeout, check=False, **kwargs):
    process = subprocess.Popen(argv, start_new_session=True, **kwargs)
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Vivado launchers spawn the implementation/simulator process. Killing
        # only the launcher lets that work continue after its receipt says FAIL.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise
    if check and returncode:
        raise subprocess.CalledProcessError(returncode, argv)
    return subprocess.CompletedProcess(argv, returncode)
