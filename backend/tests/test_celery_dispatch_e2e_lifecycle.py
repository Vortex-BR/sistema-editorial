import os
import subprocess

import pytest

from tests.test_celery_dispatch_e2e import (
    _process_group_options,
    _python_subprocess,
    _raise_body_failure,
    stop_process,
)


def test_process_lifecycle_releases_redirected_log(tmp_path):
    environment = dict(os.environ)
    python_executable = _python_subprocess(environment)
    log_path = tmp_path / "child.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [python_executable, "-c", "import time; time.sleep(30)"],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            **_process_group_options(),
        )
        stop_process(process, timeout_seconds=5)

    assert process.poll() is not None
    log_path.unlink()
    assert not log_path.exists()


def test_process_lifecycle_closes_streams_for_exited_process():
    environment = dict(os.environ)
    python_executable = _python_subprocess(environment)
    process = subprocess.Popen(
        [python_executable, "-c", "pass"],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_process_group_options(),
    )
    process.wait(timeout=5)

    stop_process(process, timeout_seconds=5)

    assert process.stdin.closed
    assert process.stdout.closed
    assert process.stderr.closed


@pytest.mark.skipif(os.name != "nt", reason="taskkill is the Windows fallback")
def test_process_lifecycle_uses_taskkill_only_after_timeout(monkeypatch):
    class TimedOutProcess:
        pid = 43210
        stdin = None
        stdout = None
        stderr = None

        def __init__(self):
            self.returncode = None
            self.terminated = False
            self.wait_calls = 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired(self.pid, timeout)
            self.returncode = 1
            return self.returncode

    taskkill_calls = []

    def fake_run(command, **kwargs):
        taskkill_calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    process = TimedOutProcess()

    stop_process(process, timeout_seconds=0.1)

    assert process.terminated
    assert process.wait_calls == 2
    assert taskkill_calls[0][0] == ["taskkill", "/PID", "43210", "/T", "/F"]


def test_process_lifecycle_preserves_body_failure_when_cleanup_also_fails():
    original = AssertionError("functional failure")
    cleanup = RuntimeError("cleanup failure")
    try:
        raise original
    except AssertionError as exc:
        body_failure = (exc, exc.__traceback__)

    with pytest.raises(AssertionError) as raised:
        _raise_body_failure(body_failure, [cleanup])

    assert raised.value is original
    assert raised.value.__cause__.exceptions == (cleanup,)
