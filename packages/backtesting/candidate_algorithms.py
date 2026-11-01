"""Candidate weekly NIFTY rules, fixed before their results were seen. Research only.

Each rule reads the signal at arming and names a template, or names nothing and sits the week out.
The template's own entry and exit rules then run unchanged. Nothing here reaches paper trading, and
the frozen forward period does not use these rules. Results are simulated history, not a forecast.

Development uses expiries before HOLDOUT_START. At most three rules advance, and the holdout stage
is meant to run once on those rules alone.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from math import sqrt
from statistics import mean, stdev

from packages.backtesting.entry_signals import EntrySignal
from packages.paper_trading.strategies import StrategyDefinition, StrategyLeg

HOLDOUT_START = date(2025, 11, 1)
_HIGH_RANK = 2 / 3
_LOW_RANK = 1 / 3
_LARGE_MOVE = 1.0
MINIMUM_DEVELOPMENT_TRADES = 15
DEVELOPMENT_T = 1.5
MAXIMUM_ADVANCED = 3
MINIMUM_HOLDOUT_TRADES = 15
# A two-sided 5% test shared across the three rules that can reach the holdout (Bonferroni).
HOLDOUT_T = 2.45
MAXIMUM_WORST_TRADE = 20000.0
MAXIMUM_DRAWDOWN = 60000.0
# Lot sizes changed over the period, so trades compare at the current one.
CURRENT_LOT = 65


def _wide(key: str, name: str, *legs: StrategyLeg) -> StrategyDefinition:
    return StrategyDefinition(key, name, "Research structure, not a paper template.", legs)


# Round 2. One-strike wings risk about what charges cost, so these use wider ones. They are not in
# the paper strategy registry, so paper trading cannot arm them.
WIDE_STRUCTURES = {
    item.key: item for item in (
        _wide("wide_iron_condor", "Wide iron condor",
              StrategyLeg("PE", "BUY", -9), StrategyLeg("PE", "SELL", -4),
              StrategyLeg("CE", "SELL", 4), StrategyLeg("CE", "BUY", 9)),
        _wide("late_wide_iron_condor", "Late wide iron condor",
              StrategyLeg("PE", "BUY", -8), StrategyLeg("PE", "SELL", -3),
              StrategyLeg("CE", "SELL", 3), StrategyLeg("CE", "BUY", 8)),
        _wide("wide_iron_butterfly", "Wide iron butterfly",
              StrategyLeg("PE", "BUY", -6), StrategyLeg("PE", "SELL", 0),
              StrategyLeg("CE", "SELL", 0), StrategyLeg("CE", "BUY", 6)),
        _wide("wide_bull_put_spread", "Wide bull put spread",
              StrategyLeg("PE", "SELL", -4), StrategyLeg("PE", "BUY", -9)),
        _wide("wide_bear_call_spread", "Wide bear call spread",
              StrategyLeg("CE", "SELL", 4), StrategyLeg("CE", "BUY", 9)),
        _wide("wide_bull_call_spread", "Wide bull call spread",
              StrategyLeg("CE", "BUY", 0), StrategyLeg("CE", "SELL", 6)),
        _wide("wide_bear_put_spread", "Wide bear put spread",
              StrategyLeg("PE", "BUY", 0), StrategyLeg("PE", "SELL", -6)),
    )
}


def _rich(signal: EntrySignal) -> bool:
    return signal.premium_rank is not None and signal.premium_rank >= _HIGH_RANK


def _cheap(signal: EntrySignal) -> bool:
    return signal.premium_rank is not None and signal.premium_rank <= _LOW_RANK


def _when_rich(key: str) -> Callable[[EntrySignal], str | None]:
    return lambda signal: key if _rich(signal) else None


def _when_cheap(key: str) -> Callable[[EntrySignal], str | None]:
    return lambda signal: key if _cheap(signal) else None


def _with_trend(up: str, down: str) -> Callable[[EntrySignal], str | None]:
    def pick(signal: EntrySignal) -> str | None:
        if signal.above_trend and signal.momentum > 0:
            return up
        if not signal.above_trend and signal.momentum < 0:
            return down
        return None
    return pick


def _against_large_move(up: str, down: str) -> Callable[[EntrySignal], str | None]:
    def pick(signal: EntrySignal) -> str | None:
        if signal.move_size <= -_LARGE_MOVE:
            return up
        return down if signal.move_size >= _LARGE_MOVE else None
    return pick


def _fade_skew(signal: EntrySignal) -> str | None:
    if signal.skew_rank is None:
        return None
    if signal.skew_rank >= _HIGH_RANK:
        return "bull_put_spread"
    if signal.skew_rank <= _LOW_RANK:
        return "bear_call_spread"
    return None


def _volatility_switch(signal: EntrySignal) -> str | None:
    if _rich(signal):
        return "iron_butterfly"
    return "long_straddle" if _cheap(signal) else None


@dataclass(frozen=True)
class CandidateAlgorithm:
    name: str
    idea: str
    templates: tuple[str, ...]
    pick: Callable[[EntrySignal], str | None]
    entry_days: int = 7
    # Templates taken every week as the comparison; empty means this rule's own.
    compared_with: tuple[str, ...] = ()

    @property
    def comparison(self) -> tuple[str, ...]:
        return self.compared_with or self.templates


CANDIDATE_ALGORITHMS = (
    CandidateAlgorithm("rich_vol_condor", "Sell a range when options are dear against movement",
                       ("iron_condor",), _when_rich("iron_condor")),
    CandidateAlgorithm("rich_vol_butterfly", "Sell the centre when options are dear",
                       ("iron_butterfly",), _when_rich("iron_butterfly")),
    CandidateAlgorithm("cheap_vol_straddle", "Buy movement when options are cheap",
                       ("long_straddle",), _when_cheap("long_straddle")),
    CandidateAlgorithm("cheap_vol_strangle", "Buy wider movement when options are cheap",
                       ("long_strangle",), _when_cheap("long_strangle")),
    CandidateAlgorithm("trend_debit_spread", "Buy a spread in the trend's direction",
                       ("bull_call_spread", "bear_put_spread"),
                       _with_trend("bull_call_spread", "bear_put_spread")),
    CandidateAlgorithm("trend_credit_spread", "Sell a spread on the side away from the trend",
                       ("bull_put_spread", "bear_call_spread"),
                       _with_trend("bull_put_spread", "bear_call_spread")),
    CandidateAlgorithm("trend_long_option", "Buy one option in the trend's direction",
                       ("long_call", "long_put"), _with_trend("long_call", "long_put")),
    CandidateAlgorithm("fade_large_move", "Sell a spread against a large five-session move",
                       ("bull_put_spread", "bear_call_spread"),
                       _against_large_move("bull_put_spread", "bear_call_spread")),
    CandidateAlgorithm("fade_skew", "Sell the side the option market prices highest",
                       ("bull_put_spread", "bear_call_spread"), _fade_skew),
    CandidateAlgorithm("volatility_switch", "Sell dear options and buy cheap ones",
                       ("iron_butterfly", "long_straddle"), _volatility_switch),
    CandidateAlgorithm("late_rich_butterfly", "Sell the centre closer to expiry when dear",
                       ("iron_butterfly",), _when_rich("iron_butterfly"), entry_days=3),
    CandidateAlgorithm("late_cheap_straddle", "Buy movement closer to expiry when cheap",
                       ("long_straddle",), _when_cheap("long_straddle"), entry_days=3),
    CandidateAlgorithm("wide_condor", "Sell a wide range every week",
                       ("wide_iron_condor",), lambda signal: "wide_iron_condor",
                       compared_with=("iron_condor",)),
    CandidateAlgorithm("wide_condor_when_rich", "Sell a wide range when options are dear",
                       ("wide_iron_condor",), _when_rich("wide_iron_condor")),
    CandidateAlgorithm("late_wide_condor", "Sell a wide range closer to expiry every week",
                       ("late_wide_iron_condor",), lambda signal: "late_wide_iron_condor",
                       entry_days=3, compared_with=("iron_condor",)),
    CandidateAlgorithm("wide_butterfly", "Sell the centre with wide wings every week",
                       ("wide_iron_butterfly",), lambda signal: "wide_iron_butterfly",
                       compared_with=("iron_butterfly",)),
    CandidateAlgorithm("wide_butterfly_when_rich", "Sell the centre with wide wings when dear",
                       ("wide_iron_butterfly",), _when_rich("wide_iron_butterfly")),
    CandidateAlgorithm("wide_trend_credit", "Sell a far spread on the side away from the trend",
                       ("wide_bull_put_spread", "wide_bear_call_spread"),
                       _with_trend("wide_bull_put_spread", "wide_bear_call_spread"),
                       compared_with=("bull_put_spread", "bear_call_spread")),
    CandidateAlgorithm("wide_fade_move", "Sell a far spread against a large five-session move",
                       ("wide_bull_put_spread", "wide_bear_call_spread"),
                       _against_large_move("wide_bull_put_spread", "wide_bear_call_spread"),
                       compared_with=("bull_put_spread", "bear_call_spread")),
    CandidateAlgorithm("wide_trend_debit", "Buy a wide spread in the trend's direction",
                       ("wide_bull_call_spread", "wide_bear_put_spread"),
                       _with_trend("wide_bull_call_spread", "wide_bear_put_spread"),
                       compared_with=("bull_call_spread", "bear_put_spread")),
)


@dataclass(frozen=True)
class WeeklyResult:
    expiry: date
    net_pnl: float


@dataclass(frozen=True)
class CandidateResult:
    trades: int
    average_net_pnl: float
    win_rate: float
    t_value: float | None
    worst_trade: float
    drawdown: float
    total_net_pnl: float


def summarize_results(results: Sequence[WeeklyResult]) -> CandidateResult:
    values = [item.net_pnl for item in sorted(results, key=lambda item: item.expiry)]
    if not values:
        return CandidateResult(0, 0.0, 0.0, None, 0.0, 0.0, 0.0)
    peak = running = drawdown = 0.0
    for value in values:
        running += value
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    spread = stdev(values) if len(values) > 1 else 0.0
    return CandidateResult(
        trades=len(values), average_net_pnl=mean(values),
        win_rate=sum(value > 0 for value in values) / len(values),
        t_value=mean(values) / (spread / sqrt(len(values))) if spread > 0 else None,
        worst_trade=min(values), drawdown=drawdown, total_net_pnl=sum(values),
    )


def advances_from_development(result: CandidateResult, every_week: CandidateResult) -> bool:
    return (
        result.trades >= MINIMUM_DEVELOPMENT_TRADES
        and result.average_net_pnl > 0
        and result.t_value is not None and result.t_value >= DEVELOPMENT_T
        and result.average_net_pnl > every_week.average_net_pnl
        and result.win_rate > every_week.win_rate
    )


def holdout_failures(result: CandidateResult, every_week: CandidateResult) -> tuple[str, ...]:
    """Empty only when the rule passes every test on expiries it was never developed on."""
    gates = {
        "makes money after charges": result.average_net_pnl > 0,
        f"at least {MINIMUM_HOLDOUT_TRADES} trades": result.trades >= MINIMUM_HOLDOUT_TRADES,
        "clear of chance": result.t_value is not None and result.t_value >= HOLDOUT_T,
        "earns more than trading every week": (
            result.average_net_pnl > every_week.average_net_pnl
        ),
        "wins more often than trading every week": result.win_rate > every_week.win_rate,
        "worst trade within the per-trade limit": -result.worst_trade <= MAXIMUM_WORST_TRADE,
        "drawdown within the limit": result.drawdown <= MAXIMUM_DRAWDOWN,
    }
    return tuple(name for name, passed in gates.items() if not passed)
