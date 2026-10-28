"""Replay a registered option strategy over imported NIFTY option candles.

Usage:
  PYTHONPATH=. python scripts/run-history-backtest.py --strategy iron_condor \
      --from 2025-01-01 --to 2026-10-27 --stop 3000 --target 1500

Reads the private history imported by scripts/import-history.py; makes no provider
calls. Prints a JSON summary with chronological folds and every skipped expiry.
"""

import argparse
import json
from datetime import date, time
from decimal import Decimal

from apps.api.settings import get_settings
from packages.backtesting.history_backtest import BacktestConfig, run_backtest
from packages.database.session import build_session_factory
from packages.history.loader import load_histories
from packages.paper_trading.strategies import get_strategy


def main(args: argparse.Namespace) -> None:
    strategy = get_strategy(args.strategy)
    if strategy is None:
        raise SystemExit(f"Unknown strategy {args.strategy!r}")
    config = BacktestConfig(
        strategy=strategy, lots=args.lots, entry_days_before_expiry=args.entry_days,
        entry_time=time.fromisoformat(args.entry_time),
        stop_loss_amount=Decimal(args.stop) if args.stop else None,
        target_profit_amount=Decimal(args.target) if args.target else None,
        half_spread_pct=Decimal(args.half_spread_pct),
    )
    factory = build_session_factory(get_settings().database_url)
    with factory() as session:
        histories, rejected = load_histories(
            session, interval=args.interval, start=args.start, end=args.end
        )
    report = run_backtest(histories, config)
    summary = report.summary(folds=args.folds)
    summary["expiries_not_loaded"] = [
        {"expiry": expiry.isoformat(), "reason": reason} for expiry, reason in rejected
    ]
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay a strategy over imported candles")
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--lots", type=int, default=1)
    parser.add_argument("--entry-days", type=int, default=4)
    parser.add_argument("--entry-time", default="09:30")
    parser.add_argument("--stop", default="")
    parser.add_argument("--target", default="")
    parser.add_argument("--half-spread-pct", default="1")
    parser.add_argument("--folds", type=int, default=4)
    main(parser.parse_args())
