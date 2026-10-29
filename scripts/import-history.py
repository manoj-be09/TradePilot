"""Import expired NIFTY option candles from Upstox into the configured database.

Usage:
  PYTHONPATH=. python scripts/import-history.py --from 2024-01-01 --to 2026-10-27 --dry-run
  PYTHONPATH=. python scripts/import-history.py --from 2024-01-01 --to 2026-10-27 --interval 5m

Read-only provider calls only; needs an Upstox plan that includes Expired Instruments.
Re-running resumes: COMPLETE and EMPTY contracts are skipped unless --force is given.
The imported data is private and must never be redistributed.
"""

import argparse
import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Engine, func, select

from apps.api.settings import get_settings
from packages.database.base import Base
from packages.database.history_models import HistoricalImportContract, HistoricalOptionBar
from packages.database.session import build_session_factory
from packages.history.importer import (
    HistoryImportError,
    ImportSettings,
    import_expiry,
    list_expiries,
    reference_spot,
    select_contracts,
)
from packages.market_data.contracts import MarketDataProviderError, MarketDataUnavailable
from packages.market_data.provider import get_market_data_provider
from packages.market_data.upstox import UpstoxMarketDataProvider


async def main(args: argparse.Namespace) -> None:
    provider = get_market_data_provider()
    if not isinstance(provider, UpstoxMarketDataProvider):
        raise SystemExit("Configured Upstox provider required")
    settings = ImportSettings(
        interval=args.interval, strike_window_pct=Decimal(str(args.strike_pct)),
        lookback_days=args.lookback_days, pause_seconds=args.pause, force=args.force,
    )
    today = datetime.now(timezone.utc).date()
    expiries = await list_expiries(provider, args.start, args.end, today)
    if args.max_expiries:
        expiries = expiries[-args.max_expiries:]
    print(f"{len(expiries)} expired NIFTY expiries between {args.start} and {args.end}")
    if args.dry_run:
        total = 0
        for expiry in expiries:
            try:
                spot = await reference_spot(provider, expiry)
                listed = await provider.get_expired_option_contracts("NIFTY", expiry)
                count = len(select_contracts(listed, expiry, spot, settings.strike_window_pct))
            except (HistoryImportError, MarketDataUnavailable, MarketDataProviderError) as error:
                print(f"  {expiry}: not planned ({type(error).__name__}: {error})")
                continue
            total += count
            print(f"  {expiry}: reference {spot}, {count} contracts")
            await asyncio.sleep(settings.pause_seconds)
        minutes = total * settings.pause_seconds / 60
        print(f"Dry run: {total} candle requests, at least {minutes:.0f} minutes at this pace")
        return

    factory = build_session_factory(get_settings().database_url)
    engine = factory.kw["bind"]
    if isinstance(engine, Engine) and engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine, tables=[
            HistoricalOptionBar.__table__, HistoricalImportContract.__table__,
        ])
    for expiry in expiries:
        summary = await import_expiry(provider, factory, expiry, settings)
        counts = ", ".join(f"{key.lower()} {value}" for key, value in summary.counts.items())
        print(
            f"  {expiry}: {summary.planned} planned, {summary.skipped} already done, {counts}, "
            f"{summary.bars} candles {summary.detail}".rstrip()
        )
    with factory() as session:
        rows = session.execute(
            select(HistoricalImportContract.status, func.count())
            .where(HistoricalImportContract.interval == settings.interval)
            .group_by(HistoricalImportContract.status)
        ).all()
    print("Coverage for", settings.interval, dict(rows))


def _date(value: str) -> date:
    return date.fromisoformat(value)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import expired NIFTY option candles")
    parser.add_argument("--from", dest="start", type=_date, required=True)
    parser.add_argument("--to", dest="end", type=_date, required=True)
    parser.add_argument("--interval", default="5m", choices=["1m", "3m", "5m", "15m", "30m", "1d"])
    parser.add_argument("--strike-pct", type=float, default=3.0)
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument("--max-expiries", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(main(parser.parse_args()))
