"""Compare other exit rules with a template's own on exactly the same entries. Research only.

The rule sets and the way they are judged are fixed here before any result is seen. The earlier
half of the expiries picks the best rule set for each strategy, and the later half, which played
no part in that choice, judges it. Paper trading and the Trade page never read this. Results are
simulated history, not a forecast.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import sqrt
from statistics import mean, stdev

from packages.backtesting.history_backtest import BacktestTrade, ExitRuleSet

EXIT_RULE_SETS = (
    ExitRuleSet("no_trail", use_trail=False),
    ExitRuleSet("wide_target", target_multiple=Decimal(2)),
    ExitRuleSet("no_target", target_multiple=None),
    ExitRuleSet("charge_floor", charges_floor_multiple=Decimal(3)),
    ExitRuleSet("leave_early", leave_before_expiry_day=True),
    ExitRuleSet("hold", use_stop=False, target_multiple=None),
)
# About 2.5% of chance results clear this in one direction, roughly 0.65 of 26 strategies.
_CLEAR_T = 2.0
_MINIMUM_TRADES = 15


@dataclass(frozen=True)
class HalfResult:
    trades: int
    average_current: float
    average_rules: float
    average_gain: float
    t_value: float | None


@dataclass(frozen=True)
class ExitRuleVerdict:
    best_rules: str
    earlier: HalfResult
    later: HalfResult
    holds_up: bool


def compare_half(trades: Sequence[BacktestTrade], name: str) -> HalfResult:
    gains = [float(trade.alternatives[name].net_pnl - trade.net_pnl) for trade in trades]
    spread = stdev(gains) if len(gains) > 1 else 0.0
    return HalfResult(
        trades=len(trades),
        average_current=mean(float(trade.net_pnl) for trade in trades) if trades else 0.0,
        average_rules=(
            mean(float(trade.alternatives[name].net_pnl) for trade in trades) if trades else 0.0
        ),
        average_gain=mean(gains) if gains else 0.0,
        t_value=mean(gains) / (spread / sqrt(len(gains))) if spread > 0 else None,
    )


def split_at_middle(trades: Sequence[BacktestTrade]) -> tuple[list[BacktestTrade], ...]:
    ordered = sorted(trades, key=lambda trade: trade.expiry)
    middle = len(ordered) // 2
    return ordered[:middle], ordered[middle:]


def judge(trades: Sequence[BacktestTrade]) -> ExitRuleVerdict | None:
    """Pick on the earlier half; the later half must confirm it by a clear margin and make money.

    Returns None when a half is too small to say anything.
    """
    earlier, later = split_at_middle(trades)
    if len(earlier) < _MINIMUM_TRADES or len(later) < _MINIMUM_TRADES:
        return None
    best = max(EXIT_RULE_SETS, key=lambda rules: compare_half(earlier, rules.name).average_gain)
    held_back = compare_half(later, best.name)
    return ExitRuleVerdict(
        best_rules=best.name,
        earlier=compare_half(earlier, best.name),
        later=held_back,
        holds_up=(
            held_back.average_rules > 0
            and held_back.t_value is not None
            and held_back.t_value >= _CLEAR_T
        ),
    )
