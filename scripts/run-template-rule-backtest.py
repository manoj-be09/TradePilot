"""Replay every option template over imported NIFTY candles using its real paper rules.

Usage:
  PYTHONPATH=. python scripts/run-template-rule-backtest.py --from 2024-10-01 --to 2026-10-27

One trade per weekly expiry, armed on the session the forward runner would arm it (about a
week before expiry). Nothing is tuned, so the whole period counts. Writes
work/bt/<prefix>-<strategy>.json, which the Trade page ranks by. Reads private local history
only and makes no provider calls. Simulated results, not a forecast.
"""

import argparse
import json
from datetime import date, time
from decimal import Decimal
from pathlib import Path

from apps.api.settings import get_settings
from packages.backtesting.history_backtest import BacktestConfig, run_backtest
from packages.backtesting.past_tests import DEFAULT_RESULTS_DIRECTORY, TEMPLATE_RULES_MODEL_ID
from packages.database.session import build_session_factory
from packages.history.loader import load_histories
from packages.paper_trading.strategies import STRATEGIES


def main(args: argparse.Namespace) -> None:
    settings = get_settings()
    factory = build_session_factory(settings.database_url)
    with factory() as session:
        histories, _ = load_histories(session, interval=args.interval, start=args.start,
                                      end=args.end)
    limit = Decimal(args.account) * settings.paper_max_trade_risk_pct / 100
    args.directory.mkdir(parents=True, exist_ok=True)
    for strategy in STRATEGIES:
        report = run_backtest(histories, BacktestConfig(
            strategy=strategy, template_rules=True, max_trade_risk=limit,
            entry_days_before_expiry=args.entry_days,
            entry_time=time.fromisoformat(args.entry_time),
            half_spread_pct=Decimal(args.half_spread_pct),
            square_off_minutes_before_close=settings.paper_expiry_square_off_minutes_before_close,
            margin_parameters=settings.paper_margin_parameters,
        ))
        summary = {"results_model": TEMPLATE_RULES_MODEL_ID, **report.summary()}
        path = args.directory / f"{args.prefix}-{strategy.key}.json"
        path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        overall = summary["overall"]
        print(strategy.key, overall["closed_trades"], overall["net_pnl"], overall["win_rate"],
              f"skipped {summary['expiries_skipped']}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay templates with their real rules")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--entry-days", type=int, default=7)
    parser.add_argument("--entry-time", default="09:20")
    parser.add_argument("--half-spread-pct", default="0.25")
    parser.add_argument("--account", default="1000000")
    parser.add_argument("--prefix", default="rules")
    parser.add_argument("--directory", type=Path, default=DEFAULT_RESULTS_DIRECTORY)
    main(parser.parse_args())
