"""Test options rules published online over imported NIFTY candles. Research only.

Usage:
  PYTHONPATH=. python scripts/run-published-strategies.py
  PYTHONPATH=. python scripts/run-published-strategies.py --holdout rule_a rule_b

The first form loads development expiries only and picks which rules advance. The second reports
only the named rules, on holdout expiries, and is meant to run once. Writes
work/bt/published-*.json, which come from private data, so keep them out of git. Simulated results,
not a forecast.
"""

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean

from sqlalchemy import select

from apps.api.settings import get_settings
from packages.backtesting.entry_signals import read_signals
from packages.backtesting.history_backtest import Candle, ExpiryHistory, _ist
from packages.backtesting.past_tests import DEFAULT_RESULTS_DIRECTORY
from packages.backtesting.published_strategies import (
    HOLDOUT_START,
    MAXIMUM_ADVANCED,
    PUBLISHED_RULES,
    Bar,
    Book,
    Kind,
    Market,
    advances,
    holdout_failures,
    summarize_trades,
)
from packages.database.history_models import HistoricalImportContract, HistoricalOptionBar
from packages.database.session import build_session_factory

_FIRST_EXPIRY = date(2024, 10, 1)
_LAST_EXPIRY = date(2026, 10, 27)


def load_books(database_url: str, last_expiry: date) -> list[Book]:
    bars: dict[date, dict[tuple[Decimal, Kind], dict[datetime, Bar]]] = {}
    with build_session_factory(database_url)() as session:
        rows = session.execute(
            select(HistoricalOptionBar.expiry, HistoricalOptionBar.strike,
                   HistoricalOptionBar.option_type, HistoricalOptionBar.bar_start,
                   HistoricalOptionBar.open, HistoricalOptionBar.high, HistoricalOptionBar.low,
                   HistoricalOptionBar.close)
            .join(HistoricalImportContract, (
                HistoricalImportContract.instrument_key == HistoricalOptionBar.instrument_key
            ) & (HistoricalImportContract.interval == HistoricalOptionBar.interval))
            .where(HistoricalOptionBar.interval == "5m",
                   HistoricalImportContract.status == "COMPLETE",
                   HistoricalOptionBar.expiry >= _FIRST_EXPIRY,
                   HistoricalOptionBar.expiry <= last_expiry)
        )
        for expiry, strike, kind, start, opened, high, low, closed in rows:
            series = bars.setdefault(expiry, {}).setdefault((strike, kind), {})
            series[_ist(start)] = Bar(float(opened), float(high), float(low), float(closed))
    return [Book(expiry, by_key) for expiry, by_key in sorted(bars.items())]


def as_history(book: Book) -> ExpiryHistory:
    return ExpiryHistory(expiry=book.expiry, lot_size=65, bars={
        key: [Candle(start=moment, close=Decimal(str(bar.close)))
              for moment, bar in sorted(series.items())]
        for key, series in book.bars.items()
    })


def main(args: argparse.Namespace) -> None:
    holdout = args.holdout is not None
    end = _LAST_EXPIRY if holdout else HOLDOUT_START - timedelta(days=1)
    books = load_books(get_settings().database_url, end)
    print("expiries loaded", len(books), flush=True)
    signals = read_signals([as_history(book) for book in books], 7)
    market = Market(books, float(args.half_spread_pct), signals)
    chosen = [rule for rule in PUBLISHED_RULES if not holdout or rule.name in args.holdout]
    rows = {}
    for rule in chosen:
        trades = [trade for trade in rule.run(market)
                  if (trade.expiry >= HOLDOUT_START) == holdout]
        rows[rule.name] = (rule, trades, summarize_trades(trades))
        print("ran", rule.name, len(trades), flush=True)
    advanced = sorted(
        (name for name, row in rows.items() if advances(row[2])),
        key=lambda name: -(rows[name][2].t_value or 0.0),
    )[:MAXIMUM_ADVANCED]
    output = {}
    for name, (rule, trades, result) in rows.items():
        verdict: object = holdout_failures(result) if holdout else name in advanced
        gross = mean(trade.gross_pnl for trade in trades) if trades else 0.0
        charges = mean(trade.charges for trade in trades) if trades else 0.0
        output[name] = {"source": rule.source, "defined_risk": rule.defined_risk,
                        "average_gross_after_spread": gross, "average_charges": charges,
                        "result": asdict(result), "failed" if holdout else "advances": verdict}
        t_value = "n/a" if result.t_value is None else f"{result.t_value:.2f}"
        print(name, result.trades, round(result.average_net_pnl), f"{result.win_rate:.0%}",
              f"t {t_value}", "gross", round(gross), "charges", round(charges),
              "total", round(result.total_net_pnl), "worst", round(result.worst_trade),
              "drawdown", round(result.drawdown), verdict, flush=True)
    stage = "holdout" if holdout else "development"
    args.directory.mkdir(parents=True, exist_ok=True)
    path = args.directory / f"published-{stage}-{args.half_spread_pct}.json"
    path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test published options rules")
    parser.add_argument("--holdout", nargs="+", default=None,
                        help="Rules that advanced; omit to run the development stage")
    parser.add_argument("--half-spread-pct", default="0.25")
    parser.add_argument("--directory", type=Path, default=DEFAULT_RESULTS_DIRECTORY)
    main(parser.parse_args())
