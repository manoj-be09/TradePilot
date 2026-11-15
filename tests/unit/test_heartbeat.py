"""The heartbeat pings the watchdog only while the worker is healthy, and survives outages."""

import asyncio

import httpx

from apps.heartbeat.main import WORKER_READY, beat

URL = "https://hc.example/ping/abc"


def _run(worker_status: int | None, watchdog_down: bool = False) -> tuple[bool, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if str(request.url) == WORKER_READY:
            if worker_status is None:
                raise httpx.ConnectError("api down")
            return httpx.Response(worker_status)
        if watchdog_down:
            raise httpx.ConnectError("watchdog down")
        return httpx.Response(200)

    async def go() -> bool:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await beat(client, URL)

    return asyncio.run(go()), seen


def test_a_healthy_worker_sends_the_plain_ping():
    assert _run(200) == (True, [WORKER_READY, URL])


def test_an_unhealthy_worker_or_a_dead_api_reports_failure_at_once():
    assert _run(503) == (False, [WORKER_READY, URL + "/fail"])
    assert _run(None) == (False, [WORKER_READY, URL + "/fail"])


def test_a_watchdog_outage_is_logged_not_raised():
    assert _run(200, watchdog_down=True)[0] is True
