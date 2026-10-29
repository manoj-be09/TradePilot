"""Replay a registered option strategy over imported NIFTY option candles.

Usage:
  PYTHONPATH=. python scripts/run-history-backtest.py --strategy iron_condor \
      --from 2025-01-01 --to 2026-10-27 --stop 3000 --target 1500

Reads the private history imported by scripts/import-history.py; makes no provider
calls. With --spreads-from/--spreads-to, fills use half-spreads calibrated per premium
band from option quotes the recorder saved in that window instead of the flat default.
Prints a JSON summary with chronological folds and every skipped expiry. With
--save-baseline, also stores the run's maximum drawdown per lot as the baseline that
automatic paper demotion compares against (refused below 10 trades).
"""

import argparse
import json
from datetime import date, time
from decimal import Decimal

from apps.api.settings import get_settings
from packages.backtesting.history_backtest import MODEL_ID, BacktestConfig, run_backtest
from packages.backtesting.spread_model import SpreadModel, calibrate_spreads
from packages.database.session import build_session_factory
from packages.history.loader import load_histories
from packages.history.spreads import recorded_option_quotes
from packages.paper_trading.demotion import record_drawdown_baseline
from packages.paper_trading.strategies import get_strategy


def main(args: argparse.Namespace) -> None:
    strategy = get_strategy(args.strategy)
    if strategy is None:
        raise SystemExit(f"Unknown strategy {args.strategy!r}")
    factory = build_session_factory(get_settings().database_url)
    spread_model: SpreadModel | None = None
    if bool(args.spreads_from) != bool(args.spreads_to):
        raise SystemExit("Pass both --spreads-from and --spreads-to, or neither")
    if args.spreads_from:
        with factory() as session:
            spread_model = calibrate_spreads(
                recorded_option_quotes(session, start=args.spreads_from, end=args.spreads_to),
                Decimal(args.half_spread_pct),
            )
    config = BacktestConfig(
        strategy=strategy, lots=args.lots, entry_days_before_expiry=args.entry_days,
        entry_time=time.fromisoformat(args.entry_time),
        stop_loss_amount=Decimal(args.stop) if args.stop else None,
        target_profit_amount=Decimal(args.target) if args.target else None,
        half_spread_pct=Decimal(args.half_spread_pct),
        spread_model=spread_model,
    )
    with factory() as session:
        histories, rejected = load_histories(
            session, interval=args.interval, start=args.start, end=args.end
        )
    report = run_backtest(histories, config)
    summary = report.summary(folds=args.folds)
    summary["expiries_not_loaded"] = [
        {"expiry": expiry.isoformat(), "reason": reason} for expiry, reason in rejected
    ]
    if args.save_baseline:
        with factory() as session:
            baseline = record_drawdown_baseline(
                session, strategy.key, report.pnl_per_lot(), source=MODEL_ID,
                details={
                    "from": args.start.isoformat(), "to": args.end.isoformat(),
                    "interval": args.interval, "config": config.snapshot(),
                },
            )
            session.commit()
            summary["saved_baseline"] = {
                "max_drawdown_per_lot": baseline.max_drawdown_per_lot,
                "trades": baseline.trades,
            }
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
    parser.add_argument("--spreads-from", type=date.fromisoformat, default=None)
    parser.add_argument("--spreads-to", type=date.fromisoformat, default=None)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--save-baseline", action="store_true")
    main(parser.parse_args())
