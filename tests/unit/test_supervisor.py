"""The supervisor restarts crashed processes with growing waits and leaves clean exits alone."""

import logging
import subprocess
import sys

from apps.supervisor.main import (
    FIRST_BACKOFF_SECONDS,
    MAX_BACKOFF_SECONDS,
    STEADY_SECONDS,
    Service,
    Supervisor,
    _spawn_with_log,
    configure_logging,
    local_services,
)


class _Process:
    pid = 1

    def __init__(self) -> None:
        self.code: int | None = None

    def poll(self) -> int | None:
        return self.code


class _Spawner:
    def __init__(self, error: OSError | None = None) -> None:
        self.started: list[_Process] = []
        self.error = error

    def __call__(self, _service: Service) -> _Process:
        if self.error:
            raise self.error
        self.started.append(_Process())
        return self.started[-1]


SERVICE = Service("worker", ("python", "-m", "worker"))


def test_starts_everything_once_while_it_keeps_running():
    spawner = _Spawner()
    supervisor = Supervisor([SERVICE, Service("api", ("python",))], spawner)
    supervisor.step(0.0)
    supervisor.step(10.0)
    assert len(spawner.started) == 2


def test_a_crash_restarts_after_a_wait_that_grows_each_time():
    spawner = _Spawner()
    supervisor = Supervisor([SERVICE], spawner)
    supervisor.step(0.0)
    spawner.started[-1].code = 1
    supervisor.step(1.0)
    supervisor.step(1.0 + FIRST_BACKOFF_SECONDS - 0.5)
    assert len(spawner.started) == 1
    supervisor.step(1.0 + FIRST_BACKOFF_SECONDS)
    assert len(spawner.started) == 2

    now = 1.0 + FIRST_BACKOFF_SECONDS
    spawner.started[-1].code = 1
    supervisor.step(now)
    supervisor.step(now + FIRST_BACKOFF_SECONDS)
    assert len(spawner.started) == 2
    supervisor.step(now + FIRST_BACKOFF_SECONDS * 2)
    assert len(spawner.started) == 3


def test_the_wait_is_capped_and_resets_after_a_steady_run():
    spawner = _Spawner()
    supervisor = Supervisor([SERVICE], spawner)
    now = 0.0
    supervisor.step(now)
    for _ in range(12):
        spawner.started[-1].code = 1
        supervisor.step(now)
        now += MAX_BACKOFF_SECONDS
        supervisor.step(now)
    assert supervisor.backoff["worker"] == MAX_BACKOFF_SECONDS

    now += STEADY_SECONDS
    supervisor.step(now)
    assert "worker" not in supervisor.backoff


def test_a_clean_exit_is_not_restarted():
    spawner = _Spawner()
    supervisor = Supervisor([SERVICE], spawner)
    supervisor.step(0.0)
    spawner.started[-1].code = 0
    supervisor.step(1.0)
    supervisor.step(10_000.0)
    assert len(spawner.started) == 1


def test_a_failure_to_start_is_retried_rather_than_fatal():
    spawner = _Spawner(OSError("missing"))
    supervisor = Supervisor([SERVICE], spawner)
    supervisor.step(0.0)
    assert "worker" not in supervisor.running
    spawner.error = None
    supervisor.step(FIRST_BACKOFF_SECONDS)
    assert len(spawner.started) == 1


def test_one_crash_does_not_touch_the_other_services():
    spawner = _Spawner()
    supervisor = Supervisor([SERVICE, Service("api", ("python",))], spawner)
    supervisor.step(0.0)
    spawner.started[0].code = 1
    supervisor.step(1.0)
    assert supervisor.running["api"] is spawner.started[1]
    assert "worker" not in supervisor.running


def test_the_local_set_includes_every_process_the_paper_period_needs():
    names = {service.name for service in local_services("python")}
    assert names == {"api", "web", "worker", "forward-runner", "recorder", "alert-relay",
                     "nightly"}


def test_a_real_process_is_logged_to_its_own_file_and_its_exit_code_seen(tmp_path):
    spawn = _spawn_with_log(tmp_path)
    process = spawn(Service("demo", (sys.executable, "-c", "print('hello'); raise SystemExit(3)")))
    assert isinstance(process, subprocess.Popen)
    assert process.wait(timeout=30) == 3
    assert "hello" in (tmp_path / "work" / "logs" / "demo.log").read_text()


def test_the_supervisor_logs_to_stdout_and_its_own_file_never_stderr(tmp_path, capfd):
    previous = list(logging.getLogger().handlers)
    try:
        configure_logging(tmp_path)
        logging.getLogger("supervisor").info("started")
        for handler in logging.getLogger().handlers:
            handler.flush()
        captured = capfd.readouterr()
        assert "started" not in captured.err
        assert "started" in (tmp_path / "work" / "logs" / "supervisor.log").read_text()
    finally:
        for handler in logging.getLogger().handlers:
            handler.close()
        logging.getLogger().handlers[:] = previous
