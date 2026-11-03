"""Test options rules published online over imported NIFTY candles. Research only.

Usage:
  PYTHONPATH=. python scripts/run-published-strategies.py
  PYTHONPATH=. python scripts/run-published-strategies.py --holdout rule_a rule_b
  PYTHONPATH=. python scripts/run-published-strategies.py --study global
  PYTHONPATH=. python scripts/run-published-strategies.py --study rich --extra-database FILE
  PYTHONPATH=. python scripts/run-published-strategies.py --study night --forward \\
      --extra-database FILE

The global, rich and night studies price fills from recorded spread bands in
work/bt/cal-bear_call_spread.json, at the median by default. The rich and night studies need the far
strikes from a private extra history database, and leave special sessions out of their bars. A
forward run scores only expiries after the imported history ended.

The first form loads development expiries only and picks which rules advance. The second reports
only the named rules, on holdout expiries, and is meant to run once. Writes
work/bt/published-*.json, which come from private data, so keep them out of git. Simulated results,
not a forecast.
"""

import argparse
import json
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean

from apps.api.settings import get_settings
from packages.backtesting.book_loader import load_books, recorded_spread
from packages.backtesting.entry_signals import read_signals
from packages.backtesting.global_strategies import GLOBAL_RULES
from packages.backtesting.history_backtest import Candle, ExpiryHistory
from packages.backtesting.past_tests import DEFAULT_RESULTS_DIRECTORY
from packages.backtesting.published_strategies import (
    HOLDOUT_START,
    MAXIMUM_ADVANCED,
    PUBLISHED_RULES,
    Book,
    Market,
    advances,
    holdout_failures,
    summarize_trades,
)
from packages.backtesting.rich_premium import night_rules, rich_rules

_LAST_EXPIRY = date(2026, 10, 27)


def as_history(book: Book) -> ExpiryHistory:
    return ExpiryHistory(expiry=book.expiry, lot_size=65, bars={
        key: [Candle(start=moment, close=Decimal(str(bar.close)))
              for moment, bar in sorted(series.items())]
        for key, series in book.bars.items()
    })


def main(args: argparse.Namespace) -> None:
    holdout = args.holdout is not None
    forward = args.forward
    end = date.max if forward else _LAST_EXPIRY if holdout else HOLDOUT_START - timedelta(days=1)
    rich = args.study in ("rich", "night")
    books = load_books(get_settings().database_url, end,
                       [f"sqlite:///{path.as_posix()}" for path in args.extra_database],
                       regular_hours_only=rich)
    print("expiries loaded", len(books), flush=True)
    signals = {} if rich else read_signals([as_history(book) for book in books], 7)
    rules = {"global": GLOBAL_RULES, "published": PUBLISHED_RULES,
             "rich": rich_rules(args.entry_delay_bars),
             "night": night_rules(args.entry_delay_bars)}[args.study]
    banded = args.study in ("global", "rich", "night")
    spread = recorded_spread(args.spread_band) if banded else float(args.half_spread_pct)
    market = Market(books, spread, signals)
    chosen = [rule for rule in rules if not holdout or rule.name in args.holdout]
    rows = {}
    for rule in chosen:
        trades = [trade for trade in rule.run(market)
                  if (trade.expiry > _LAST_EXPIRY if forward
                      else (trade.expiry >= HOLDOUT_START) == holdout)]
        rows[rule.name] = (rule, trades, summarize_trades(trades))
        print("ran", rule.name, len(trades), flush=True)
    advanced = sorted(
        (name for name, row in rows.items() if advances(row[2])),
        key=lambda name: -(rows[name][2].t_value or 0.0),
    )[:MAXIMUM_ADVANCED]
    output = {}
    for name, (rule, trades, result) in rows.items():
        judged = holdout or forward
        verdict: object = holdout_failures(result) if judged else name in advanced
        gross = mean(trade.gross_pnl for trade in trades) if trades else 0.0
        charges = mean(trade.charges for trade in trades) if trades else 0.0
        output[name] = {"source": rule.source, "defined_risk": rule.defined_risk,
                        "average_gross_after_spread": gross, "average_charges": charges,
                        "result": asdict(result), "failed" if judged else "advances": verdict}
        t_value = "n/a" if result.t_value is None else f"{result.t_value:.2f}"
        print(name, result.trades, round(result.average_net_pnl), f"{result.win_rate:.0%}",
              f"t {t_value}", "gross", round(gross), "charges", round(charges),
              "total", round(result.total_net_pnl), "worst", round(result.worst_trade),
              "drawdown", round(result.drawdown), verdict, flush=True)
    stage = "forward" if forward else "holdout" if holdout else "development"
    args.directory.mkdir(parents=True, exist_ok=True)
    label = args.spread_band if banded else args.half_spread_pct
    delay = f"-delay{args.entry_delay_bars}" if args.entry_delay_bars else ""
    path = args.directory / f"{args.study}-{stage}-{label}{delay}.json"
    path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test published options rules")
    parser.add_argument("--holdout", nargs="+", default=None,
                        help="Rules that advanced; omit to run the development stage")
    parser.add_argument("--half-spread-pct", default="0.25")
    parser.add_argument("--study", choices=("published", "global", "rich", "night"),
                        default="published")
    parser.add_argument("--forward", action="store_true",
                        help="Score only expiries after the imported history ended")
    parser.add_argument("--extra-database", type=Path, action="append", default=[],
                        help="Another private SQLite history file to merge in")
    parser.add_argument("--entry-delay-bars", type=int, default=0,
                        help="Rich and night studies: enter this many 5-minute bars later")
    parser.add_argument("--spread-band", choices=("median_pct", "half_spread_pct"),
                        default="median_pct")
    parser.add_argument("--directory", type=Path, default=DEFAULT_RESULTS_DIRECTORY)
    main(parser.parse_args())
