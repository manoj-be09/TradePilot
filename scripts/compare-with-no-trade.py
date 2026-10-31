"""Print how each saved template replay compares with NO_TRADE. Research only.

Usage:
  PYTHONPATH=. python scripts/compare-with-no-trade.py

Reads work/bt/rules-*.json from scripts/run-template-rule-backtest.py and writes nothing. These
results come from private data, so keep the output out of git. Simulated, not a forecast.
"""

import argparse
import json
from pathlib import Path

from packages.backtesting.no_trade import compare_with_no_trade
from packages.backtesting.past_tests import DEFAULT_RESULTS_DIRECTORY


def main(directory: Path) -> None:
    results = []
    for path in sorted(directory.glob("rules-*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        results.append(compare_with_no_trade(
            raw["config"]["strategy_key"], raw["overall"], raw["folds"],
        ))
    for result in sorted(results, key=lambda item: -item.average_net_pnl):
        t_value = "n/a" if result.t_value is None else f"{result.t_value:.2f}"
        print(result.strategy_key, result.trades, round(result.average_net_pnl), t_value,
              "QUALIFIES" if result.qualifies else f"abstain: {'; '.join(result.failed_gates)}")
    print(f"{sum(item.qualifies for item in results)} of {len(results)} qualify")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare template replays with NO_TRADE")
    parser.add_argument("--directory", type=Path, default=DEFAULT_RESULTS_DIRECTORY)
    main(parser.parse_args().directory)
