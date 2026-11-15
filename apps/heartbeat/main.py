"""Run with python -m apps.heartbeat.main; pings an outside watchdog while paper trading is healthy.

Idle unless HEARTBEAT_URL is set (a healthchecks.io-style check URL). Each cycle asks the local
API whether the paper exit worker is cycling: healthy sends a ping, unhealthy sends `<url>/fail`.
If the machine, the API or this process stops, pings stop and the watchdog alerts on its own,
which a check running on this machine could never do. Reads health only; never touches a trade.
"""

import argparse
import asyncio
import logging

import httpx

from apps.api.settings import get_settings

logger = logging.getLogger("heartbeat")
WORKER_READY = "http://127.0.0.1:8000/ready/paper-worker"


async def beat(client: httpx.AsyncClient, url: str) -> bool:
    """One cycle; True when the worker was healthy. Never raises on a network error."""
    try:
        healthy = (await client.get(WORKER_READY)).status_code == 200
    except httpx.HTTPError:
        healthy = False
    try:
        await client.get(url if healthy else f"{url.rstrip('/')}/fail")
    except httpx.HTTPError as error:
        logger.warning("Watchdog ping failed: %s", type(error).__name__)
    return healthy


async def serve(interval_seconds: float, once: bool) -> None:
    secret = get_settings().heartbeat_url
    url = secret.get_secret_value().strip() if secret else ""
    if not url:
        logger.info("HEARTBEAT_URL is not set; the heartbeat is idle")
        return
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            logger.info("Worker %s", "healthy" if await beat(client, url) else "NOT healthy")
            if once:
                return
            await asyncio.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ping an outside watchdog")
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request log would print the URL
    asyncio.run(serve(args.interval, args.once))


if __name__ == "__main__":
    main()
