"""Compare other exit rules with each template's own, on the same entries. Research only.

Usage:
  PYTHONPATH=. python scripts/run-exit-rule-comparison.py --from 2024-10-01 --to 2026-10-27

Writes work/bt/exits-<strategy>.json, which nothing else reads. Reads private local history only
and makes no provider calls. Simulated results, not a forecast.
"""

import argparse
import json
from dataclasses import asdict
from datetime import date, time
from decimal import Decimal
from pathlib import Path

from apps.api.settings import get_settings
from packages.backtesting.exit_rules import EXIT_RULE_SETS, compare_half, judge, split_at_middle
from packages.backtesting.history_backtest import BacktestConfig, _stats, run_backtest
from packages.backtesting.past_tests import DEFAULT_RESULTS_DIRECTORY
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
            exit_rule_sets=EXIT_RULE_SETS,
            entry_days_before_expiry=args.entry_days,
            entry_time=time.fromisoformat(args.entry_time),
            half_spread_pct=Decimal(args.half_spread_pct),
            square_off_minutes_before_close=settings.paper_expiry_square_off_minutes_before_close,
            margin_parameters=settings.paper_margin_parameters,
        ))
        trades = report.trades
        verdict = judge(trades)
        earlier, later = split_at_middle(trades)
        result = {
            "strategy_key": strategy.key,
            "expiries_skipped": len(report.skipped),
            "current": _stats(trades),
            "rule_sets": {
                rules.name: {
                    "overall": _stats([trade.alternatives[rules.name] for trade in trades]),
                    "earlier": asdict(compare_half(earlier, rules.name)),
                    "later": asdict(compare_half(later, rules.name)),
                }
                for rules in EXIT_RULE_SETS
            },
            "verdict": asdict(verdict) if verdict else None,
        }
        path = args.directory / f"exits-{strategy.key}.json"
        path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(strategy.key, len(trades), verdict.best_rules if verdict else "too few",
              verdict.holds_up if verdict else "", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare exit rules on the same entries")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--entry-days", type=int, default=7)
    parser.add_argument("--entry-time", default="09:20")
    parser.add_argument("--half-spread-pct", default="0.25")
    parser.add_argument("--account", default="1000000")
    parser.add_argument("--directory", type=Path, default=DEFAULT_RESULTS_DIRECTORY)
    main(parser.parse_args())
