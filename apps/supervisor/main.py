"""Run with python -m apps.supervisor.main; keeps the local paper processes running.

Starts the API, web app, paper worker, forward runner, recorder, alert relay and nightly
job, each with its own log under work/logs, and restarts one that crashes, waiting longer after each
crash. A clean exit (code 0) is left alone: the relay is idle without Telegram settings and
the runner finishes at the end of its period. It only starts this repository's own programs.
"""

import argparse
import logging
import os
import signal
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("supervisor")
FIRST_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 300.0
STEADY_SECONDS = 600.0
LOCAL_DATABASE = "sqlite:///./work/nifty-local.db"


@dataclass(frozen=True)
class Service:
    name: str
    command: tuple[str, ...]


def _script(name: str) -> tuple[str, ...]:
    return (
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", f"scripts/{name}",
    )


def local_services(python: str) -> list[Service]:
    return [
        Service("api", _script("start-api-local.ps1")),
        Service("web", _script("start-web-local.ps1")),
        Service("worker", _script("start-paper-worker-local.ps1")),
        Service("forward-runner", _script("start-forward-runner-local.ps1")),
        Service("recorder", (python, "-u", "-m", "apps.recorder.main",
                             "--data-dir", "work/recorder-data")),
        Service("alert-relay", (python, "-u", "-m", "apps.alert_relay.main")),
        Service("nightly", (python, "-u", "-m", "apps.nightly.main")),
    ]


class Process(Protocol):
    pid: int

    def poll(self) -> int | None: ...


Spawn = Callable[[Service], Process]


class Supervisor:
    """One step() checks every service; time is passed in so tests need no real waiting."""

    def __init__(self, services: Sequence[Service], spawn: Spawn) -> None:
        self.services = list(services)
        self.spawn = spawn
        self.running: dict[str, Process] = {}
        self.started_at: dict[str, float] = {}
        self.backoff: dict[str, float] = {}
        self.restart_at: dict[str, float] = {}
        self.finished: set[str] = set()

    def _start(self, service: Service, now: float) -> None:
        try:
            self.running[service.name] = self.spawn(service)
        except OSError as error:
            logger.error("%s could not start: %s", service.name, type(error).__name__)
            self._schedule_restart(service.name, now)
            return
        self.started_at[service.name] = now
        self.restart_at.pop(service.name, None)
        logger.info("%s started", service.name)

    def _schedule_restart(self, name: str, now: float) -> None:
        wait = self.backoff.get(name, FIRST_BACKOFF_SECONDS)
        self.restart_at[name] = now + wait
        self.backoff[name] = min(wait * 2, MAX_BACKOFF_SECONDS)
        logger.warning("%s restarts in %.0f seconds", name, wait)

    def step(self, now: float) -> None:
        for service in self.services:
            name = service.name
            if name in self.finished:
                continue
            process = self.running.get(name)
            if process is None:
                if now >= self.restart_at.get(name, now):
                    self._start(service, now)
                continue
            code = process.poll()
            if code is None:
                if now - self.started_at[name] >= STEADY_SECONDS:
                    self.backoff.pop(name, None)
                continue
            del self.running[name]
            if code == 0:
                logger.info("%s finished cleanly and is left stopped", name)
                self.finished.add(name)
                continue
            logger.error("%s exited with code %s", name, code)
            if now - self.started_at[name] >= STEADY_SECONDS:
                self.backoff.pop(name, None)
            self._schedule_restart(name, now)


def _spawn_with_log(root: Path) -> Spawn:
    logs = root / "work" / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    def spawn(service: Service) -> Process:
        with (logs / f"{service.name}.log").open("ab") as log:
            return subprocess.Popen(  # nosec B603
                service.command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                env=_child_environment(),
            )

    return spawn


def _child_environment() -> dict[str, str]:


    return {**os.environ, "DATABASE_URL": LOCAL_DATABASE, "PYTHONPATH": ".",
            "PYTHONIOENCODING": "utf-8"}


def _stop_tree(process: Process) -> None:
    # The start scripts run the real program as a child, so stop the whole tree.
    if sys.platform == "win32":
        subprocess.run(  # nosec B603 B607
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False, capture_output=True,
        )
    elif isinstance(process, subprocess.Popen):
        process.terminate()


def serve(root: Path, interval_seconds: float) -> None:
    supervisor = Supervisor(local_services(sys.executable), _spawn_with_log(root))
    stopping: list[int] = []
    signal.signal(signal.SIGINT, lambda number, _frame: stopping.append(number))
    signal.signal(signal.SIGTERM, lambda number, _frame: stopping.append(number))
    while not stopping:
        supervisor.step(time.monotonic())
        time.sleep(interval_seconds)
    logger.info("Stopping everything")
    for process in supervisor.running.values():
        _stop_tree(process)


def configure_logging(root: Path) -> None:
    # Write the file here and use stdout: Windows PowerShell 5.1 treats a native program's
    # redirected stderr as a terminating error, which killed this process on its first line.
    logs = root / "work" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(logs / "supervisor.log", encoding="utf-8"),
        ],
    )


def main() -> None:
    configure_logging(Path.cwd())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    serve(Path.cwd(), args.interval)


if __name__ == "__main__":
    main()
