"""Walk-forward evaluation of one strategy's settings over imported NIFTY option candles.

Usage:
  PYTHONPATH=. python scripts/run-walk-forward.py --strategy bear_call_spread \
      --from 2024-10-01 --to 2026-10-27 [--train 26 --test 13] [--half-spread-pct 0.25]

Candidates are every combination of entry days before expiry (--entry-days, default
1,2,4) and stop loss in rupees per position (--stops, default none,2000,4000). Each
window picks the candidate with the best training net P&L and scores it on the next,
unseen expiries. Reads private local history only; makes no provider calls. Prints a
JSON summary. Simulated results, not a forecast.
"""

import argparse
import json
from datetime import date, time
from decimal import Decimal
from itertools import product

from apps.api.settings import get_settings
from packages.backtesting.history_backtest import BacktestConfig
from packages.backtesting.spread_model import SpreadModel, calibrate_spreads
from packages.backtesting.walk_forward import Candidate, walk_forward
from packages.database.session import build_session_factory
from packages.history.loader import load_histories
from packages.history.spreads import recorded_option_quotes
from packages.paper_trading.strategies import get_strategy


def _candidates(entry_days: str, stops: str) -> list[Candidate]:
    days = [int(item) for item in entry_days.split(",")]
    stop_values = [None if item == "none" else Decimal(item) for item in stops.split(",")]
    return [
        Candidate(
            f"entry {day}d, stop {'none' if stop is None else f'₹{stop}'}",
            {"entry_days_before_expiry": day, "stop_loss_amount": stop},
        )
        for day, stop in product(days, stop_values)
    ]


def main(args: argparse.Namespace) -> None:
    strategy = get_strategy(args.strategy)
    if strategy is None:
        raise SystemExit(f"Unknown strategy {args.strategy!r}")
    settings = get_settings()
    factory = build_session_factory(settings.database_url)
    spread_model: SpreadModel | None = None
    if bool(args.spreads_from) != bool(args.spreads_to):
        raise SystemExit("Pass both --spreads-from and --spreads-to, or neither")
    if args.spreads_from:
        with factory() as session:
            spread_model = calibrate_spreads(
                recorded_option_quotes(session, start=args.spreads_from, end=args.spreads_to),
                Decimal(args.half_spread_pct),
            )
    base = BacktestConfig(
        strategy=strategy, entry_time=time.fromisoformat(args.entry_time),
        half_spread_pct=Decimal(args.half_spread_pct), spread_model=spread_model,
        margin_parameters=settings.paper_margin_parameters,
    )
    with factory() as session:
        histories, _ = load_histories(session, interval=args.interval, start=args.start,
                                      end=args.end)
    report = walk_forward(histories, base, _candidates(args.entry_days, args.stops),
                          train_size=args.train, test_size=args.test)
    print(json.dumps(report.summary(), indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Walk-forward evaluation by expiry")
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--entry-time", default="09:30")
    parser.add_argument("--entry-days", default="1,2,4")
    parser.add_argument("--stops", default="none,2000,4000")
    parser.add_argument("--train", type=int, default=26)
    parser.add_argument("--test", type=int, default=13)
    parser.add_argument("--half-spread-pct", default="1")
    parser.add_argument("--spreads-from", type=date.fromisoformat, default=None)
    parser.add_argument("--spreads-to", type=date.fromisoformat, default=None)
    main(parser.parse_args())
