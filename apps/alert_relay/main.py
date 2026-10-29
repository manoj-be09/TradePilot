"""Run with python -m apps.alert_relay.main; forwards paper alerts to Telegram.

Idle unless TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID and TELEGRAM_ALERT_USER_EMAIL are all
set. Reads the database only; never places or changes a trade. A message that fails to
send is retried on the next few cycles and then dropped, so an outage cannot flood the
chat later.
"""

import argparse
import asyncio
import logging
from datetime import datetime, timezone

import httpx

from apps.api.settings import get_settings
from packages.alerts.relay import PREFIX, AlertCollector
from packages.alerts.telegram import TelegramError, TelegramSender
from packages.database.session import build_session_factory

logger = logging.getLogger("alert_relay")
_ATTEMPTS = 3


async def serve(interval_seconds: float, once: bool = False) -> None:
    settings = get_settings()
    token = settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else ""
    if not (token and settings.telegram_chat_id and settings.telegram_alert_user_email):
        logger.info("Telegram alerts are not configured; the relay is idle")
        return
    factory = build_session_factory()
    collector = AlertCollector(
        user_email=settings.telegram_alert_user_email, since=datetime.now(timezone.utc),
        interval_seconds=settings.paper_worker_interval_seconds,
    )
    pending: list[tuple[str, int]] = []
    async with httpx.AsyncClient() as client:
        sender = TelegramSender(token, settings.telegram_chat_id, client)
        await _deliver(sender, [(f"{PREFIX}: alerts are on.", 0)])
        while True:
            with factory() as session:
                fresh = collector.collect(session, datetime.now(timezone.utc))
            pending = await _deliver(sender, pending + [(text, 0) for text in fresh])
            if once:
                return
            await asyncio.sleep(interval_seconds)


async def _deliver(sender: TelegramSender, queue: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Send in order; return what should be retried next cycle."""
    retry: list[tuple[str, int]] = []
    for index, (text, attempts) in enumerate(queue):
        try:
            await sender.send(text)
        except TelegramError as error:
            logger.warning("%s", error)
            # Keep order: this and every later message wait for the next cycle; only the
            # failed one uses up an attempt.
            retry = ([(text, attempts + 1)] if attempts + 1 < _ATTEMPTS else []) + queue[index + 1:]
            break
    return retry


def main() -> None:
    parser = argparse.ArgumentParser(description="Forward paper alerts to Telegram")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(serve(args.interval, args.once))
    except KeyboardInterrupt:
        logger.info("Alert relay stopped")


if __name__ == "__main__":
    main()
