"""Bounded read-only live-feed check; never prints a token or WebSocket URL.

Run from the repository root with the project Python environment:
python scripts/probe-upstox-v3-feed.py
python scripts/probe-upstox-v3-feed.py --nifty-atm-call

The NIFTY mode samples one chain/master-matched call. An optional bounded
normalized quote journal must stay under the private, Git-ignored work directory.
It does not establish exchange-origin book timing.
"""

import argparse
import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))

from packages.market_data.feed_journal import FeedJournal  # noqa: E402
from packages.market_data.provider import get_market_data_provider  # noqa: E402

INSTRUMENT = "NSE_EQ|INE002A01018"  # RELIANCE
SECONDS = 45
MAX_DISPLAYED_QUOTES = 8
MIN_OBSERVED_SPAN_SECONDS = 20.0
TOTAL_TIMEOUT_SECONDS = 75
WATCHDOG_SECONDS = 90
NIFTY_JOURNAL_ROOT = repo / "work" / "feed-observations"


def private_nifty_journal_path(path: Path) -> Path:
    """Keep provider observations in a Git-ignored private workspace folder."""
    resolved = path.resolve()
    if not resolved.is_relative_to(NIFTY_JOURNAL_ROOT.resolve()):
        raise ValueError("NIFTY journal must be under work/feed-observations")
    return resolved


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


def tick_time_labels(
    quote: Any, observed_at: datetime, clock_offset: float | None,
) -> tuple[str, str, str]:
    """Never report local receipt fallback as a measured provider tick delay."""
    if quote.timestamp_basis != "provider_tick":
        return "unknown", "unknown", "unknown"
    lag = (observed_at - quote.timestamp).total_seconds()
    adjusted = f"{lag - clock_offset:.2f}" if clock_offset is not None else "unknown"
    return quote.timestamp.isoformat(), f"{lag:.2f}", adjusted


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


def choose_nifty_atm_call(chain: Any, contracts: list[Any],
                          expiry: Any) -> tuple[str, str, str] | None:
    """Accept only a quoted call matching the fetched contract master."""
    if chain.underlying != "NIFTY" or chain.expiry != expiry or chain.underlying_spot is None:
        return None
    by_token = {item.instrument_key: item for item in contracts}
    eligible = []
    for leg in chain.contracts:
        contract = by_token.get(leg.instrument_token)
        quote = leg.quote
        if (
            contract is not None
            and contract.expiry == leg.expiry == expiry
            and contract.strike == leg.strike
            and contract.option_type == leg.option_type == "CE"
            and quote.bid is not None and quote.bid > 0
            and quote.ask is not None and quote.ask >= quote.bid
            and leg.bid_quantity is not None and leg.bid_quantity > 0
            and leg.ask_quantity is not None and leg.ask_quantity > 0
        ):
            eligible.append(leg)
    if not eligible:
        return None
    chosen = min(eligible, key=lambda leg: (abs(leg.strike - chain.underlying_spot), leg.strike))
    return chosen.instrument_token, expiry.isoformat(), str(chosen.strike)


async def nifty_atm_call(provider: Any) -> tuple[str, str, str] | None:
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    contracts = await provider.get_option_contracts("NIFTY")
    expiries = sorted({item.expiry for item in contracts if item.expiry >= today})
    if not expiries:
        return None
    expiry = expiries[0]
    chain = await provider.get_option_chain("NIFTY", expiry)
    return choose_nifty_atm_call(chain, contracts, expiry)


async def main(provider: Any = None, journal: FeedJournal | None = None,
               nifty_option: bool = False) -> None:
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
        instrument = INSTRUMENT
        if nifty_option:
            selected = await nifty_atm_call(provider)
            if selected is None:
                print("No chain/master-matched quoted NIFTY call; no stream attempted.")
                return
            instrument, expiry, strike = selected
            print(f"Read-only NIFTY call probe: expiry={expiry}; strike={strike}")
        previous_price: object | None = None
        previous_book: tuple[object, ...] | None = None
        received = price_changes = book_changes = 0
        book_bases: set[str] = set()
        first_observed_at: datetime | None = None
        last_observed_at: datetime | None = None
        last_change_elapsed_seconds = 0.0
        largest_gap_seconds = 0.0
        try:
            async with asyncio.timeout(SECONDS):
                async for quote in provider.subscribe([instrument]):
                    observed_at = datetime.now(timezone.utc)
                    if journal is not None:
                        if received == 0:
                            journal.record("connected")
                        elif last_observed_at is not None and (
                            observed_at - last_observed_at
                        ).total_seconds() > 5:
                            journal.record("stale")
                        journal.record("quote", quote)
                    changed_price, changed_book, book = observation_changes(
                        previous_price, previous_book, quote
                    )
                    received += 1
                    price_changes += int(changed_price)
                    book_changes += int(changed_book)
                    book_bases.add(quote.book_timestamp_basis)
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
                    tick_at, tick_lag_label, adjusted_lag = tick_time_labels(
                        quote, observed_at, clock_offset,
                    )
                    trade_lag = (
                        (observed_at - quote.last_trade_time).total_seconds()
                        if quote.last_trade_time else None
                    )
                    trade_lag_label = f"{trade_lag:.2f}" if trade_lag is not None else "unknown"
                    if received <= MAX_DISPLAYED_QUOTES and not nifty_option:
                        print(
                            f"#{received} observed={observed_at.isoformat()} "
                            f"tick={tick_at} "
                            f"trade={trade_at} "
                            f"last={quote.last_price} bid={quote.bid}/{quote.bid_quantity} "
                            f"ask={quote.ask}/{quote.ask_quantity} "
                            f"price_changed={changed_price} book_changed={changed_book} "
                            f"receive_minus_tick_s={tick_lag_label} "
                            f"clock_adjusted_tick_lag_estimate_s={adjusted_lag} "
                            f"receive_minus_trade_s={trade_lag_label} "
                            f"book_basis={quote.book_timestamp_basis}"
                        )
                    previous_price, previous_book = quote.last_price, book
        except TimeoutError:
            pass
        finally:
            if journal is not None:
                journal.record("disconnected")
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
        if nifty_option:
            print("NIFTY option book bases: " + ", ".join(sorted(book_bases)))
        print(probe_conclusion(
            status, end_status, price_changes, book_changes,
            last_change_elapsed_seconds,
        ))
    except TimeoutError:
        raise
    except Exception as error:
        # Upstream exceptions can embed the authorized URL; never print their text.
        print(f"Probe failed ({type(error).__name__}); no market-data claim made.")


def run_nifty_probe_with_watchdog(journal_path: Path | None = None) -> None:
    """Bound the process even if Windows cannot start or interrupt its event loop."""
    try:
        command = [sys.executable, str(Path(__file__).resolve()), "--nifty-atm-call", "--child"]
        if journal_path is not None:
            command.extend(["--journal", str(private_nifty_journal_path(journal_path))])
        result = subprocess.run(
            command,
            capture_output=True, text=True, timeout=WATCHDOG_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        print("NIFTY probe exceeded its process deadline; no market-data claim made.")
        return
    except OSError as error:
        print(f"NIFTY probe could not start ({type(error).__name__}); no market-data claim made.")
        return
    print(result.stdout, end="")
    if result.returncode:
        # Child stderr may contain a provider URL; never relay it.
        print("NIFTY probe process failed; no market-data claim made.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bounded read-only feed verification")
    parser.add_argument("--journal", type=Path, help="Optional bounded JSONL observation file")
    parser.add_argument("--nifty-atm-call", action="store_true",
                        help="Probe one chain/master-matched NIFTY call without saving quotes")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.nifty_atm_call and args.journal is not None:
        try:
            args.journal = private_nifty_journal_path(args.journal)
        except ValueError:
            parser.error("NIFTY journal must be under work/feed-observations")
    if args.nifty_atm_call and not args.child:
        run_nifty_probe_with_watchdog(args.journal)
        raise SystemExit(0)
    journal = FeedJournal(args.journal) if args.journal else None
    try:
        async def bounded() -> None:
            async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
                await main(journal=journal, nifty_option=args.nifty_atm_call)

        asyncio.run(bounded())
    except TimeoutError:
        print("Probe timed out; no market-data claim made.")
    finally:
        if journal is not None:
            journal.record("stopped")
            journal.close()
