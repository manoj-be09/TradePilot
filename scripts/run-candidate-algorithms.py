"""Test the fixed candidate rules over imported NIFTY candles. Research only.

Usage:
  PYTHONPATH=. python scripts/run-candidate-algorithms.py
  PYTHONPATH=. python scripts/run-candidate-algorithms.py --holdout rule_a rule_b

The first form loads development expiries only and picks which rules advance. The second loads
everything but reports only the named rules, on holdout expiries, and is meant to run once. Writes
work/bt/candidates-*.json, which come from private data, so keep them out of git. Simulated
results, not a forecast.
"""

import argparse
import json
from dataclasses import asdict
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path

from apps.api.settings import get_settings
from packages.backtesting.candidate_algorithms import (
    CANDIDATE_ALGORITHMS,
    CURRENT_LOT,
    HOLDOUT_START,
    MAXIMUM_ADVANCED,
    WIDE_STRUCTURES,
    CandidateAlgorithm,
    CandidateResult,
    WeeklyResult,
    advances_from_development,
    holdout_failures,
    summarize_results,
)
from packages.backtesting.entry_signals import read_signals
from packages.backtesting.history_backtest import BacktestConfig, BacktestTrade, run_backtest
from packages.backtesting.past_tests import DEFAULT_RESULTS_DIRECTORY
from packages.database.session import build_session_factory
from packages.history.loader import load_histories
from packages.paper_trading.strategies import STRATEGIES

_BY_KEY = {strategy.key: strategy for strategy in STRATEGIES} | WIDE_STRUCTURES
_FIRST_EXPIRY = date(2024, 10, 1)
_LAST_EXPIRY = date(2026, 10, 27)


def main(args: argparse.Namespace) -> None:
    settings = get_settings()
    factory = build_session_factory(settings.database_url)
    holdout = args.holdout is not None
    end = _LAST_EXPIRY if holdout else HOLDOUT_START - timedelta(days=1)
    with factory() as session:
        histories, _ = load_histories(session, interval="5m", start=_FIRST_EXPIRY, end=end)
    print("expiries loaded", len(histories), flush=True)
    lots = {history.expiry: history.lot_size for history in histories}
    limit = Decimal(args.account) * settings.paper_max_trade_risk_pct / 100
    chosen = [rule for rule in CANDIDATE_ALGORITHMS if not holdout or rule.name in args.holdout]
    ledgers: dict[tuple[str, int], list[BacktestTrade]] = {}
    for rule in chosen:
        for key in (*rule.templates, *rule.comparison):
            if (key, rule.entry_days) in ledgers:
                continue
            ledgers[(key, rule.entry_days)] = run_backtest(histories, BacktestConfig(
                strategy=_BY_KEY[key], template_rules=True, max_trade_risk=limit,
                entry_days_before_expiry=rule.entry_days, entry_time=time(9, 20),
                half_spread_pct=Decimal(args.half_spread_pct),
                square_off_minutes_before_close=(
                    settings.paper_expiry_square_off_minutes_before_close
                ),
                margin_parameters=settings.paper_margin_parameters,
            )).trades
            print("replayed", key, rule.entry_days, flush=True)
    signals = {days: read_signals(histories, days) for days in {r.entry_days for r in chosen}}

    def in_stage(expiry: date) -> bool:
        return expiry >= HOLDOUT_START if holdout else expiry < HOLDOUT_START

    def scaled(trade: BacktestTrade) -> WeeklyResult:
        return WeeklyResult(trade.expiry, float(trade.net_pnl) * CURRENT_LOT / lots[trade.expiry])

    def evaluate(rule: CandidateAlgorithm) -> tuple[CandidateResult, CandidateResult]:
        by_expiry = {
            key: {trade.expiry: trade for trade in ledgers[(key, rule.entry_days)]}
            for key in {*rule.templates, *rule.comparison}
        }
        taken = []
        for expiry, signal in signals[rule.entry_days].items():
            key = rule.pick(signal)
            trade = by_expiry[key].get(expiry) if key else None
            if trade is not None and in_stage(expiry):
                taken.append(scaled(trade))
        every_week = [
            scaled(trade) for key in rule.comparison for trade in by_expiry[key].values()
            if in_stage(trade.expiry)
        ]
        return summarize_results(taken), summarize_results(every_week)

    rows = {rule.name: evaluate(rule) for rule in chosen}
    advanced = sorted(
        (name for name, row in rows.items() if advances_from_development(*row)),
        key=lambda name: -(rows[name][0].t_value or 0.0),
    )[:MAXIMUM_ADVANCED]
    output: dict[str, dict[str, object]] = {}
    for name, (result, every_week) in rows.items():
        verdict: object = holdout_failures(result, every_week) if holdout else name in advanced
        output[name] = {"rule": asdict(result), "every_week": asdict(every_week),
                        "failed" if holdout else "advances": verdict}
        t_value = "n/a" if result.t_value is None else f"{result.t_value:.2f}"
        print(name, result.trades, round(result.average_net_pnl), f"{result.win_rate:.0%}",
              f"t {t_value}", "every week", round(every_week.average_net_pnl),
              f"{every_week.win_rate:.0%}", "worst", round(result.worst_trade),
              "drawdown", round(result.drawdown), verdict, flush=True)
    stage = "holdout" if holdout else "development"
    args.directory.mkdir(parents=True, exist_ok=True)
    path = args.directory / f"candidates-{stage}-{args.half_spread_pct}.json"
    path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test the fixed candidate rules")
    parser.add_argument("--holdout", nargs="+", default=None,
                        help="Rules that advanced; omit to run the development stage")
    parser.add_argument("--half-spread-pct", default="0.25")
    parser.add_argument("--account", default="1000000")
    parser.add_argument("--directory", type=Path, default=DEFAULT_RESULTS_DIRECTORY)
    main(parser.parse_args())
