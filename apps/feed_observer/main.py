"""Read-only feed journal process; no database access or order functions."""

import argparse
import asyncio
from pathlib import Path
from typing import Any

from packages.market_data.feed_journal import FeedJournal
from packages.market_data.provider import get_market_data_provider
from packages.market_data.shared_feed import SharedQuoteFeed


async def observe(
    provider: Any, instruments: list[str], journal: FeedJournal,
    *, once: bool = False, interval: float = 30,
) -> None:
    feed = SharedQuoteFeed(provider, instruments, journal=journal)
    try:
        while True:
            try:
                market = await provider._get("/v2/market/status/NSE")
                if market.get("status") == "NORMAL_OPEN":
                    feed.start()
                elif feed.status().running:
                    await feed.stop()
            except Exception:
                # Never log private provider exceptions or authorization URLs.
                if feed.status().running:
                    await feed.stop()
            await asyncio.sleep(interval)
            feed.status()  # Expire cached observations and record observed staleness.
            if once:
                return
    finally:
        await feed.stop()


async def serve(args: argparse.Namespace) -> None:
    provider = get_market_data_provider()
    if provider.provider_id != "upstox":
        raise ValueError("Configured read-only Upstox provider required")
    journal = FeedJournal(args.journal)
    try:
        await observe(provider, args.instrument, journal, once=args.once)
    finally:
        journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only session-gated feed observation")
    parser.add_argument("--instrument", action="append", required=True)
    parser.add_argument("--journal", type=Path, default=Path("/observations/observer.jsonl"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass
    except Exception:
        print("Feed observer stopped; check provider configuration and journal access.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
