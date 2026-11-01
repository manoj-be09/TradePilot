"""Fetch the far strikes the regular history import leaves out, into a separate private database.

Read-only provider GETs through the importer's own helpers. The app's database is only read, to
learn which contracts it already has and how far NIFTY moved during each expiry's window. A contract
is wanted when its strike lies within 5% of the lowest or highest parity seen in that window.
Rerunning skips finished contracts and retries failed ones. The output file is private and stays
out of git.

Usage, from the main checkout:
  PYTHONPATH=. python scripts/import-far-strikes.py --dry-run --limit 1
  PYTHONPATH=. python scripts/import-far-strikes.py --first-expiry 2026-11-01
"""

import argparse
import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.settings import get_settings
from packages.backtesting.history_backtest import synthetic_spot
from packages.database.base import Base
from packages.database.history_models import HistoricalImportContract, HistoricalOptionBar
from packages.database.session import build_session_factory
from packages.history.importer import ImportSettings, _fetch_with_backoff, _record, validate_bars
from packages.market_data.contracts import MarketDataProviderError, MarketDataUnavailable
from packages.market_data.provider import get_market_data_provider

_WINDOW = Decimal("0.05")


def known_contracts(session: Session) -> dict[date, set[str]]:
    known: dict[date, set[str]] = {}
    for expiry, key in session.execute(
        select(HistoricalImportContract.expiry, HistoricalImportContract.instrument_key)
        .where(HistoricalImportContract.interval == "5m")
    ):
        known.setdefault(expiry, set()).add(key)
    return known


def parity_range(session: Session, expiry: date) -> tuple[Decimal, Decimal] | None:
    """The lowest and highest parity at any bar of this expiry already held."""
    chains: dict[datetime, tuple[dict[Decimal, Decimal], dict[Decimal, Decimal]]] = {}
    for strike, kind, start, close in session.execute(
        select(HistoricalOptionBar.strike, HistoricalOptionBar.option_type,
               HistoricalOptionBar.bar_start, HistoricalOptionBar.close)
        .where(HistoricalOptionBar.expiry == expiry, HistoricalOptionBar.interval == "5m")
    ):
        chain = chains.setdefault(start, ({}, {}))
        chain[0 if kind == "CE" else 1][strike] = close
    readings = [spot for calls, puts in chains.values()
                if (spot := synthetic_spot(calls, puts)) is not None]
    return (min(readings), max(readings)) if readings else None


async def main(args: argparse.Namespace) -> None:
    provider = get_market_data_provider()
    settings = ImportSettings(pause_seconds=args.pause)
    source = build_session_factory(get_settings().database_url)
    with source() as session:
        known = known_contracts(session)
    expiries = sorted(expiry for expiry in known if expiry >= args.first_expiry)
    if args.limit:
        expiries = expiries[-args.limit:]
    factory = None
    if not args.dry_run:
        factory = build_session_factory(f"sqlite:///{args.database.as_posix()}")
        Base.metadata.create_all(factory.kw["bind"], tables=[HistoricalOptionBar.__table__,
                                                             HistoricalImportContract.__table__])
    started = time.time()
    for position, expiry in enumerate(expiries, 1):
        with source() as session:
            span = parity_range(session, expiry)
        if span is None:
            print(expiry, "no parity readings", flush=True)
            continue
        low, high = span[0] * (1 - _WINDOW), span[1] * (1 + _WINDOW)
        try:
            listed = await provider.get_expired_option_contracts("NIFTY", expiry)
        except (MarketDataUnavailable, MarketDataProviderError) as error:
            print(expiry, "contract list failed", type(error).__name__, flush=True)
            continue
        wanted = [item for item in listed
                  if item.expiry == expiry and low <= item.strike <= high
                  and item.instrument_key not in known[expiry]]
        if factory is not None:
            with factory() as session:
                done = set(session.scalars(select(HistoricalImportContract.instrument_key).where(
                    HistoricalImportContract.expiry == expiry,
                    HistoricalImportContract.status.in_(("COMPLETE", "EMPTY")))))
            wanted = [item for item in wanted if item.instrument_key not in done]
        print(f"{expiry} [{position}/{len(expiries)}] listed {len(listed)} "
              f"have {len(known[expiry])} strikes {round(low)}..{round(high)} "
              f"to fetch {len(wanted)} elapsed {round(time.time() - started)}s", flush=True)
        if factory is None:
            await asyncio.sleep(settings.pause_seconds)
            continue
        start, end = expiry - timedelta(days=settings.lookback_days), expiry
        counts = {"COMPLETE": 0, "EMPTY": 0, "FAILED": 0}
        for contract in wanted:
            bars = []
            try:
                bars = sorted(await _fetch_with_backoff(provider, contract, start, end, settings,
                                                        asyncio.sleep),
                              key=lambda bar: bar.timestamp)
                reason = validate_bars(bars, start, end)
                if reason:
                    status, detail = "FAILED", reason
                elif not bars:
                    status, detail = "EMPTY", "Provider returned no candles for this range."
                else:
                    status, detail = "COMPLETE", f"{len(bars)} candles"
            except (MarketDataUnavailable, MarketDataProviderError) as error:
                status, detail = "FAILED", f"{type(error).__name__}: {error}"
            with factory() as session:
                _record(session, contract, settings, start, end, status, bars, detail,
                        datetime.now(timezone.utc))
                session.commit()
            counts[status] += 1
            await asyncio.sleep(settings.pause_seconds)
        print("   ", counts, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import far strikes into a private database")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Only the latest N expiries")
    parser.add_argument("--first-expiry", type=date.fromisoformat, default=date(2024, 10, 1))
    parser.add_argument("--pause", type=float, default=1.1)
    parser.add_argument("--database", type=Path, default=Path("work/history-wide.db"))
    asyncio.run(main(parser.parse_args()))
