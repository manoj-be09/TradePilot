"""Bounded read-only live-feed check; never prints a token or WebSocket URL.

Run from the repository root with the project Python environment:
python scripts/probe-upstox-v3-feed.py
"""

import asyncio
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))

from packages.market_data.provider import get_market_data_provider  # noqa: E402

INSTRUMENT = "NSE_EQ|INE002A01018"  # RELIANCE
SECONDS = 45
MAX_DISPLAYED_QUOTES = 8
MIN_OBSERVED_SPAN_SECONDS = 20.0


def clock_offset_seconds(
    server_date: str | None, before: datetime, after: datetime,
) -> float | None:
    """Approximate local-minus-server UTC offset from a bounded HTTP round trip."""
    if not server_date or before.tzinfo is None or after.tzinfo is None:
        return None
    try:
        server_at = parsedate_to_datetime(server_date)
    except (TypeError, ValueError):
        return None
    if server_at.tzinfo is None:
        return None
    midpoint = before + (after - before) / 2
    return (midpoint - server_at).total_seconds()


async def estimate_provider_clock_offset() -> float | None:
    """Read a public Upstox HTTP Date header without sending credentials."""
    try:
        before = datetime.now(timezone.utc)
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                "https://api.upstox.com/v2/market/status/NSE",
                headers={"Cache-Control": "no-cache"},
            )
        after = datetime.now(timezone.utc)
        return clock_offset_seconds(response.headers.get("Date"), before, after)
    except (httpx.HTTPError, ValueError):
        return None


def observation_changes(
    previous_price: object | None,
    previous_book: tuple[object, ...] | None,
    quote: Any,
) -> tuple[bool, bool, tuple[object, ...]]:
    book = (
        quote.bid, quote.ask, quote.bid_quantity, quote.ask_quantity,
    )
    return (
        previous_price is not None and quote.last_price != previous_price,
        previous_book is not None and book != previous_book,
        book,
    )


def probe_conclusion(
    before: str, after: str, price_changes: int, book_changes: int,
    last_change_elapsed_seconds: float,
) -> str:
    if before != "NORMAL_OPEN" or after != "NORMAL_OPEN" or (
        price_changes == 0 and book_changes == 0
    ) or last_change_elapsed_seconds < MIN_OBSERVED_SPAN_SECONDS:
        return "Changing market updates were not established by this run."
    return (
        "Changing provider updates observed; "
        "exchange-origin book time remains unverified."
    )


async def main(provider=None) -> None:
    provider = provider or get_market_data_provider()
    if provider.provider_id != "upstox":
        print("Upstox token is not configured; no stream attempted.")
        return
    clock_offset = await estimate_provider_clock_offset()
    if clock_offset is not None:
        print(f"Approximate local-minus-Upstox HTTP Date offset: {clock_offset:+.2f}s")
        if abs(clock_offset) > 5:
            print(
                "Local clock differs materially; raw receive-minus-tick values "
                "are not feed delay estimates."
            )
    else:
        print(
            "Clock reference unavailable; raw receive-minus-tick values may "
            "include local clock skew."
        )
    try:
        market = await provider._get("/v2/market/status/NSE")
        status = market.get("status", "unknown")
        print(f"NSE status: {status}")
        if status != "NORMAL_OPEN":
            print("Session is not NORMAL_OPEN; no live-update probe attempted.")
            return
        previous_price: object | None = None
        previous_book: tuple[object, ...] | None = None
        received = price_changes = book_changes = 0
        first_observed_at: datetime | None = None
        last_observed_at: datetime | None = None
        last_change_elapsed_seconds = 0.0
        largest_gap_seconds = 0.0
        try:
            async with asyncio.timeout(SECONDS):
                async for quote in provider.subscribe([INSTRUMENT]):
                    observed_at = datetime.now(timezone.utc)
                    changed_price, changed_book, book = observation_changes(
                        previous_price, previous_book, quote
                    )
                    received += 1
                    price_changes += int(changed_price)
                    book_changes += int(changed_book)
                    first_observed_at = first_observed_at or observed_at
                    if changed_price or changed_book:
                        last_change_elapsed_seconds = (
                            observed_at - first_observed_at
                        ).total_seconds()
                    if last_observed_at is not None:
                        largest_gap_seconds = max(
                            largest_gap_seconds,
                            (observed_at - last_observed_at).total_seconds(),
                        )
                    last_observed_at = observed_at
                    trade_at = (
                        quote.last_trade_time.isoformat()
                        if quote.last_trade_time else "unknown"
                    )
                    tick_lag = (observed_at - quote.timestamp).total_seconds()
                    trade_lag = (
                        (observed_at - quote.last_trade_time).total_seconds()
                        if quote.last_trade_time else None
                    )
                    trade_lag_label = f"{trade_lag:.2f}" if trade_lag is not None else "unknown"
                    if received <= MAX_DISPLAYED_QUOTES:
                        adjusted_lag = (
                            f"{tick_lag - clock_offset:.2f}"
                            if clock_offset is not None else "unknown"
                        )
                        print(
                            f"#{received} observed={observed_at.isoformat()} "
                            f"tick={quote.timestamp.isoformat()} "
                            f"trade={trade_at} "
                            f"last={quote.last_price} bid={quote.bid}/{quote.bid_quantity} "
                            f"ask={quote.ask}/{quote.ask_quantity} "
                            f"price_changed={changed_price} book_changed={changed_book} "
                            f"receive_minus_tick_s={tick_lag:.2f} "
                            f"clock_adjusted_tick_lag_estimate_s={adjusted_lag} "
                            f"receive_minus_trade_s={trade_lag_label} "
                            f"book_basis={quote.book_timestamp_basis}"
                        )
                    previous_price, previous_book = quote.last_price, book
        except TimeoutError:
            pass
        market_after = await provider._get("/v2/market/status/NSE")
        end_status = market_after.get("status", "unknown")
        duration = (
            (last_observed_at - first_observed_at).total_seconds()
            if first_observed_at and last_observed_at else 0.0
        )
        print(f"NSE status after probe: {end_status}")
        print(
            f"Usable quotes: {received}; price changes: {price_changes}; "
            f"bid/ask/depth changes: {book_changes}; observed span: {duration:.2f}s; "
            f"last change after: {last_change_elapsed_seconds:.2f}s; "
            f"largest receive gap: {largest_gap_seconds:.2f}s"
        )
        print(probe_conclusion(
            status, end_status, price_changes, book_changes,
            last_change_elapsed_seconds,
        ))
    except Exception as error:
        # Upstream exceptions can embed the authorized URL; never print their text.
        print(f"Probe failed ({type(error).__name__}); no market-data claim made.")


if __name__ == "__main__":
    asyncio.run(main())
