"""Compare saved template replays with NO_TRADE, which risks nothing and earns exactly zero.

A strategy qualifies only if it passes every gate; otherwise the answer is to abstain. Saved
summaries keep average wins and losses rather than each trade, so the spread used here is a lower
bound on the real one. That can only flatter a strategy: a failure is firm, a pass is provisional.
Results are simulated history, not a forecast, and nothing here starts, sizes or blocks a trade.
"""

from dataclasses import dataclass
from math import sqrt
from typing import Any

MINIMUM_TRADES = 30
# Matches the paper account's limits: 2% per trade and the 6% demotion fallback, on ₹10,00,000.
MAXIMUM_WORST_TRADE = 20000.0
MAXIMUM_DRAWDOWN = 60000.0
# A two-sided 5% test shared across all 26 strategies (Bonferroni).
CLEAR_T = 3.03


@dataclass(frozen=True)
class NoTradeComparison:
    strategy_key: str
    trades: int
    average_net_pnl: float
    t_value: float | None
    failed_gates: tuple[str, ...]

    @property
    def qualifies(self) -> bool:
        return not self.failed_gates


def _t_value(overall: dict[str, Any]) -> float | None:
    trades = overall["closed_trades"]
    if trades < 2:
        return None
    average = overall["average_net_pnl"]
    variance = (
        overall["winning_trades"] * (overall["average_win"] or 0) ** 2
        + overall["losing_trades"] * (overall["average_loss"] or 0) ** 2
    ) / trades - average**2
    return average / sqrt(variance / trades) if variance > 0 else None


def compare_with_no_trade(
    strategy_key: str, overall: dict[str, Any], folds: list[dict[str, Any]],
) -> NoTradeComparison:
    t_value = _t_value(overall)
    gates = {
        "beats NO_TRADE after charges": overall["average_net_pnl"] > 0,
        f"at least {MINIMUM_TRADES} trades": overall["closed_trades"] >= MINIMUM_TRADES,
        "worst trade within the per-trade limit": -overall["worst_trade"] <= MAXIMUM_WORST_TRADE,
        "drawdown within the limit": overall["closed_trade_drawdown"] <= MAXIMUM_DRAWDOWN,
        "clear of chance across 26 strategies": t_value is not None and t_value >= CLEAR_T,
        "made money in every fold": bool(folds) and all(fold["net_pnl"] > 0 for fold in folds),
    }
    return NoTradeComparison(
        strategy_key=strategy_key,
        trades=overall["closed_trades"],
        average_net_pnl=overall["average_net_pnl"],
        t_value=t_value,
        failed_gates=tuple(name for name, passed in gates.items() if not passed),
    )
