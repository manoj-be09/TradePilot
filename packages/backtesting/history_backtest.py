"""Replay a registered option strategy over imported historical NIFTY option candles.

Candles carry no bid/ask, so every fill is the bar close moved against us by a labelled
half-spread model. Exits are checked on bar closes only, so a move inside a bar can
realize more than the stop (gap risk). An expiry whose legs lack a common bar at entry,
or that has no bar before the expiry-day square-off, is skipped and reported, never
filled in. Spot comes from put-call parity at the strike where call and put trade
closest, because the import stores option candles only.

Each entry is also priced with the paper SPAN-style margin estimate, using volatility
implied from the entry bar closes. Capital used is the larger of that margin and the
maximum loss, the same amount a paper entry must fit in free cash, so returns can be
compared across strategies on the capital they tie up.

With template_rules the replay follows a template's real paper rules: the strike is fixed
when armed, entry waits for the entry conditions, and each trade's stop, target and trail are
sized from its own entry quotes and judged by the paper exit function. Candles have no
bid/ask, so the spread rule is judged on the half-spread model, and put/call ratio uses only
the imported strikes. exit_rule_sets also closes each of those entries under other exit rules,
for research only: paper trading never reads them.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from packages.backtesting.performance import PerformanceTrade, summarize
from packages.backtesting.spread_model import SpreadModel
from packages.core.charges import order_charges, schedule_for
from packages.options.models import PayoffLeg, PayoffRequest
from packages.options.payoff import calculate_expiry_payoff
from packages.options.pricing import _implied_volatility
from packages.paper_trading.exits import evaluate_exit
from packages.paper_trading.margin import (
    FALLBACK_VOLATILITY_PCT,
    MARGIN_MODEL_ID,
    MarginLeg,
    MarginParameters,
    estimate_position_margin,
    years_to_expiry,
)
from packages.paper_trading.rules import EntryCondition, evaluate_entry_conditions
from packages.paper_trading.strategies import StrategyDefinition
from packages.paper_trading.template_policy import entry_conditions, template_policy

MODEL_ID = "nifty-history-candle-replay-v1"
FILL_MODEL = (
    "Fills at the bar close moved against the trade by the half-spread model (flat, or "
    "per premium band from recorded quotes); no "
    "historical bid/ask, queue or depth. Exits are checked on bar closes only."
)
_IST = ZoneInfo("Asia/Kolkata")
_CENT = Decimal("0.01")
_TICK = Decimal("0.05")
ExitReason = Literal[
    "STOP_LOSS", "TARGET", "TRAILING_STOP", "EXPIRY_SQUARE_OFF", "EARLY_SQUARE_OFF",
]
OptionKind = Literal["CE", "PE"]


@dataclass(frozen=True)
class Candle:
    start: datetime
    close: Decimal
    open_interest: int | None = None


@dataclass(frozen=True)
class ExpiryHistory:
    """Bars per (strike, option type) for one expiry, each list ordered by time."""

    expiry: date
    lot_size: int
    bars: dict[tuple[Decimal, OptionKind], list[Candle]]


@dataclass(frozen=True)
class ExitRuleSet:
    """Another way to close the same entry, built from the template's own stop, target, trail."""

    name: str
    use_stop: bool = True
    # Scales the template target; None means no target and no trail.
    target_multiple: Decimal | None = Decimal(1)
    use_trail: bool = True
    # The target is at least this many times the round-trip charges at the entry prices.
    charges_floor_multiple: Decimal | None = None
    # Square off at 15:10 on the last session before expiry day.
    leave_before_expiry_day: bool = False


@dataclass(frozen=True)
class BacktestConfig:
    strategy: StrategyDefinition
    lots: int = 1
    entry_days_before_expiry: int = 4
    entry_time: time = time(9, 30)
    stop_loss_amount: Decimal | None = None
    target_profit_amount: Decimal | None = None
    half_spread_pct: Decimal = Decimal(1)
    square_off_minutes_before_close: int = 20
    # When set, replaces the flat half-spread with recorded per-premium-band spreads.
    spread_model: SpreadModel | None = None
    # Follow the template's real entry and exit rules instead of the fixed amounts above.
    template_rules: bool = False
    # Entries whose maximum loss exceeds this are skipped, as the account's per-trade limit would.
    max_trade_risk: Decimal | None = None
    # Also close each template-rule entry under these, for research.
    exit_rule_sets: tuple[ExitRuleSet, ...] = ()
    # The paper account's default PAPER_MARGIN_* settings.
    margin_parameters: MarginParameters = field(default_factory=lambda: MarginParameters(
        price_scan_pct=Decimal(9), volatility_scan_points=Decimal(4),
        exposure_pct=Decimal(2), short_option_minimum_pct=Decimal(3),
    ))

    def half_spread_for(self, price: Decimal) -> Decimal:
        if self.spread_model is None:
            return self.half_spread_pct
        return self.spread_model.half_spread_pct(price)

    def snapshot(self) -> dict[str, Any]:
        template = {"template_rules": True, "max_trade_risk": str(self.max_trade_risk)} \
            if self.template_rules else {}
        return {
            **template,
            "strategy_key": self.strategy.key,
            "strategy_version": self.strategy.version,
            "lots": self.lots,
            "entry_days_before_expiry": self.entry_days_before_expiry,
            "entry_time_ist": self.entry_time.isoformat(),
            "stop_loss_amount": str(self.stop_loss_amount) if self.stop_loss_amount else None,
            "target_profit_amount": (
                str(self.target_profit_amount) if self.target_profit_amount else None
            ),
            "half_spread_pct": str(self.half_spread_pct),
            "spread_model": self.spread_model.snapshot() if self.spread_model else None,
            "square_off_minutes_before_close": self.square_off_minutes_before_close,
            "margin_parameters": self.margin_parameters.snapshot(),
        }


@dataclass(frozen=True)
class BacktestLeg:
    strike: Decimal
    option_type: OptionKind
    side: Literal["BUY", "SELL"]
    quantity_units: int
    entry_price: Decimal
    exit_price: Decimal


@dataclass(frozen=True)
class BacktestTrade:
    expiry: date
    entry_at: datetime
    exit_at: datetime
    exit_reason: ExitReason
    synthetic_spot: Decimal
    reference_strike: Decimal
    max_loss: Decimal
    gross_pnl: Decimal
    charges: Decimal
    legs: list[BacktestLeg]
    entry_margin: Decimal
    # The same entry closed under each of config.exit_rule_sets, by name.
    alternatives: dict[str, "BacktestTrade"] = field(default_factory=dict)

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.charges

    @property
    def capital_used(self) -> Decimal:
        return max(self.max_loss, self.entry_margin)


@dataclass(frozen=True)
class SkippedExpiry:
    expiry: date
    reason: str


@dataclass
class BacktestReport:
    config: BacktestConfig
    trades: list[BacktestTrade] = field(default_factory=list)
    skipped: list[SkippedExpiry] = field(default_factory=list)

    def pnl_per_lot(self) -> list[Decimal]:
        """Net P&L per lot in closing order; the series a drawdown baseline is built from."""
        ordered = sorted(self.trades, key=lambda trade: trade.exit_at)
        return [trade.net_pnl / self.config.lots for trade in ordered]

    def summary(self, folds: int = 4) -> dict[str, Any]:
        ordered = sorted(self.trades, key=lambda trade: trade.exit_at)
        return {
            "model_id": MODEL_ID,
            "fill_model": FILL_MODEL,
            "margin_model": MARGIN_MODEL_ID,
            "config": self.config.snapshot(),
            "expiries_traded": len(ordered),
            "expiries_skipped": len(self.skipped),
            "skipped": [
                {"expiry": item.expiry.isoformat(), "reason": item.reason}
                for item in self.skipped
            ],
            "overall": _stats(ordered),
            "folds": [
                {
                    "first_expiry": chunk[0].expiry.isoformat(),
                    "last_expiry": chunk[-1].expiry.isoformat(),
                    **_stats(chunk),
                }
                for chunk in _chunks(ordered, folds)
            ],
        }


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _ist(moment: datetime) -> datetime:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return aware.astimezone(_IST)


def fill_price(close: Decimal, side: Literal["BUY", "SELL"], half_spread_pct: Decimal) -> Decimal:
    """Buys pay above the close and sells receive below it, never under one tick."""
    offset = close * half_spread_pct / Decimal(100)
    price = close + offset if side == "BUY" else close - offset
    return max(_TICK, _money(price))


def synthetic_spot(
    calls: dict[Decimal, Decimal], puts: dict[Decimal, Decimal]
) -> Decimal | None:
    """Put-call parity (K + C - P) at the strike where call and put prices are closest."""
    common = [strike for strike in calls if strike in puts]
    if not common:
        return None
    strike = min(common, key=lambda value: (abs(calls[value] - puts[value]), value))
    return strike + calls[strike] - puts[strike]


def _entry_margin(
    history: ExpiryHistory, config: BacktestConfig, entry_at: datetime, spot: Decimal,
    legs: list[tuple[Decimal, OptionKind, Literal["BUY", "SELL"], int, Decimal]],
) -> Decimal:
    """Margin estimate at entry; each leg's volatility is implied from its bar close."""
    years = years_to_expiry(history.expiry, entry_at)
    margin_legs = []
    for strike, kind, side, units, close in legs:
        implied = _implied_volatility(
            kind, float(close), float(spot), float(strike), years, 0.0, 0.0
        )
        margin_legs.append(MarginLeg(
            instrument=kind, quantity_units=units if side == "BUY" else -units,
            underlying_price=spot, strike=strike, years_to_expiry=years,
            volatility_pct=(
                Decimal(str(round(implied * 100, 4))) if implied else FALLBACK_VOLATILITY_PCT
            ),
            expires_today=entry_at.date() == history.expiry,
        ))
    return estimate_position_margin(margin_legs, config.margin_parameters).total


def _cvar(values: list[Decimal], tail: Decimal = Decimal("0.05")) -> Decimal | None:
    if not values:
        return None
    count = max(1, int(len(values) * tail))
    worst = sorted(values)[:count]
    return _money(sum(worst, Decimal(0)) / count)


def _stats(trades: Sequence[BacktestTrade]) -> dict[str, Any]:
    result = summarize([
        PerformanceTrade(net_pnl=trade.net_pnl, initial_risk=trade.max_loss,
                         exit_at=trade.exit_at)
        for trade in trades
    ])
    result["charges"] = float(sum((trade.charges for trade in trades), Decimal(0)))
    capital = [trade.capital_used for trade in trades]
    result["average_capital_used"] = (
        float(_money(sum(capital, Decimal(0)) / len(capital))) if capital else None
    )
    result["average_return_on_capital_pct"] = (
        float(_money(sum(
            (trade.net_pnl / trade.capital_used * 100 for trade in trades), Decimal(0)
        ) / len(trades))) if trades else None
    )
    win, loss = result["average_win"], result["average_loss"]
    result["payoff_ratio"] = win / -loss if win is not None and loss else None
    result["cvar_5pct"] = (
        float(value) if (value := _cvar([trade.net_pnl for trade in trades])) is not None
        else None
    )
    result["exit_reasons"] = {
        reason: sum(trade.exit_reason == reason for trade in trades)
        for reason in (
            "STOP_LOSS", "TARGET", "TRAILING_STOP", "EXPIRY_SQUARE_OFF", "EARLY_SQUARE_OFF",
        )
    }
    return result


def _chunks(trades: list[BacktestTrade], folds: int) -> list[list[BacktestTrade]]:
    """Consecutive chronological folds of near-equal size; empty folds are dropped."""
    if not trades or folds < 1:
        return []
    size, extra = divmod(len(trades), folds)
    chunks, start = [], 0
    for index in range(folds):
        end = start + size + (1 if index < extra else 0)
        if end > start:
            chunks.append(trades[start:end])
        start = end
    return chunks


def _entry_day(history: ExpiryHistory, days_before: int) -> date | None:
    """First session at or after expiry minus the offset that has any bars."""
    target = history.expiry - timedelta(days=days_before)
    days = sorted({
        _ist(candle.start).date()
        for candles in history.bars.values()
        for candle in candles
        if target <= _ist(candle.start).date() <= history.expiry
    })
    return days[0] if days else None


def run_expiry(
    history: ExpiryHistory, config: BacktestConfig
) -> BacktestTrade | SkippedExpiry:
    entry_day = _entry_day(history, config.entry_days_before_expiry)
    if entry_day is None:
        return SkippedExpiry(history.expiry, "no candles in the entry window")
    # Per (strike, type): close by IST timestamp.
    closes = {
        key: {_ist(candle.start): candle.close for candle in candles}
        for key, candles in history.bars.items()
    }
    if config.template_rules:
        return _run_template_expiry(history, config, closes, entry_day)
    entry_moments = sorted({
        moment for series in closes.values() for moment in series
        if moment.date() == entry_day and moment.time() >= config.entry_time
    })
    strikes = sorted({strike for strike, _ in history.bars})
    for entry_at in entry_moments:
        calls = {s: series[entry_at] for (s, k), series in closes.items()
                 if k == "CE" and entry_at in series}
        puts = {s: series[entry_at] for (s, k), series in closes.items()
                if k == "PE" and entry_at in series}
        spot = synthetic_spot(calls, puts)
        if spot is None:
            continue
        reference = min(strikes, key=lambda strike: (abs(strike - spot), strike))
        legs = _select_legs(config, strikes, reference)
        if legs is None:
            return SkippedExpiry(history.expiry, "strategy legs fall outside imported strikes")
        if all(entry_at in closes.get((strike, kind), {}) for strike, kind, _, _ in legs):
            return _simulate(history, config, closes, legs, entry_at, spot, reference)
    return SkippedExpiry(history.expiry, "no common entry bar for every leg and parity pair")


def _put_call_ratios(history: ExpiryHistory) -> dict[datetime, Decimal]:
    """Ratio at each bar time; a strike with no bar in that interval keeps its last reading."""
    events: dict[datetime, list[tuple[tuple[Decimal, OptionKind], int]]] = {}
    for key, candles in history.bars.items():
        for candle in candles:
            if candle.open_interest is not None:
                events.setdefault(_ist(candle.start), []).append((key, candle.open_interest))
    latest: dict[tuple[Decimal, OptionKind], int] = {}
    ratios: dict[datetime, Decimal] = {}
    for moment in sorted(events):
        latest.update(events[moment])
        calls = sum(value for (_, kind), value in latest.items() if kind == "CE")
        puts = sum(value for (_, kind), value in latest.items() if kind == "PE")
        if calls > 0:
            ratios[moment] = Decimal(puts) / Decimal(calls)
    return ratios


def _run_template_expiry(
    history: ExpiryHistory,
    config: BacktestConfig,
    closes: dict[tuple[Decimal, OptionKind], dict[datetime, Decimal]],
    entry_day: date,
) -> BacktestTrade | SkippedExpiry:
    conditions = [
        EntryCondition(item["metric"], item["operator"], Decimal(item["threshold"]))
        for item in entry_conditions(config.strategy.key)
    ]
    ratios = (
        _put_call_ratios(history)
        if any(item.metric == "put_call_ratio_oi" for item in conditions) else {}
    )
    close_dt = datetime.combine(history.expiry, time(15, 30), tzinfo=_IST)
    cutoff = close_dt - timedelta(minutes=config.square_off_minutes_before_close)
    first = datetime.combine(entry_day, config.entry_time, tzinfo=_IST)
    moments = sorted({
        moment for series in closes.values() for moment in series if first <= moment < cutoff
    })
    strikes = sorted({strike for strike, _ in history.bars})
    legs = None
    reference = Decimal(0)
    for moment in moments:
        calls = {s: series[moment] for (s, k), series in closes.items()
                 if k == "CE" and moment in series}
        puts = {s: series[moment] for (s, k), series in closes.items()
                if k == "PE" and moment in series}
        spot = synthetic_spot(calls, puts)
        if spot is None:
            continue
        if legs is None:
            # Arming fixes the strike; a later entry keeps it even if the index has moved.
            reference = min(strikes, key=lambda strike: (abs(strike - spot), strike))
            legs = _select_legs(config, strikes, reference)
            if legs is None:
                return SkippedExpiry(history.expiry, "strategy legs fall outside imported strikes")
        if not all(moment in closes.get((strike, kind), {}) for strike, kind, _, _ in legs):
            continue
        results = evaluate_entry_conditions(conditions, {
            "put_call_ratio_oi": ratios.get(moment),
            "median_relative_spread_pct": config.half_spread_pct * 2,
        })
        if all(result.passed for result in results):
            return _simulate(history, config, closes, legs, moment, spot, reference)
    return SkippedExpiry(history.expiry, "entry rules did not pass before the entry cutoff")


def _select_legs(
    config: BacktestConfig, strikes: list[Decimal], reference: Decimal
) -> list[tuple[Decimal, OptionKind, Literal["BUY", "SELL"], int]] | None:
    index = strikes.index(reference)
    legs: list[tuple[Decimal, OptionKind, Literal["BUY", "SELL"], int]] = []
    for leg in config.strategy.legs:
        position = index + leg.strike_offset
        if position < 0 or position >= len(strikes):
            return None
        legs.append((strikes[position], leg.option_type, leg.side, leg.quantity_ratio))
    return legs


def _simulate(
    history: ExpiryHistory,
    config: BacktestConfig,
    closes: dict[tuple[Decimal, OptionKind], dict[datetime, Decimal]],
    legs: list[tuple[Decimal, OptionKind, Literal["BUY", "SELL"], int]],
    entry_at: datetime,
    spot: Decimal,
    reference: Decimal,
) -> BacktestTrade | SkippedExpiry:
    entries = []
    margin_inputs = []
    entry_cash = Decimal(0)
    payoff_legs = []
    for strike, kind, side, ratio in legs:
        units = ratio * config.lots * history.lot_size
        close = closes[(strike, kind)][entry_at]
        price = fill_price(close, side, config.half_spread_for(close))
        entry_cash += -price * units if side == "BUY" else price * units
        entries.append((strike, kind, side, units, price))
        margin_inputs.append((strike, kind, side, units, close))
        payoff_legs.append(PayoffLeg(
            option_type=kind, strike=strike,
            quantity=ratio * config.lots if side == "BUY" else -ratio * config.lots,
            entry_premium=price,
        ))
    payoff = calculate_expiry_payoff(PayoffRequest(
        underlying="NIFTY", multiplier=Decimal(history.lot_size), price_min=Decimal(0),
        price_max=max(spot, max(strike for strike, *_ in legs)) * 2, grid_points=2,
        legs=payoff_legs,
    ))
    if payoff.maximum_loss_unbounded or payoff.maximum_loss is None or payoff.maximum_loss <= 0:
        return SkippedExpiry(history.expiry, "strategy has no bounded positive maximum loss")
    max_loss = _money(payoff.maximum_loss)
    if config.max_trade_risk is not None and max_loss > config.max_trade_risk:
        return SkippedExpiry(history.expiry, "maximum loss is over the per-trade risk limit")
    stop, target, trail = config.stop_loss_amount, config.target_profit_amount, None
    if config.template_rules:
        spread_cost = sum((
            2 * close * config.half_spread_for(close) / Decimal(100) * units
            for _, _, _, units, close in margin_inputs
        ), Decimal(0))
        policy = template_policy(
            config.strategy.key, max_loss,
            None if payoff.maximum_profit_unbounded else payoff.maximum_profit, spread_cost,
        )
        if policy is None:
            return SkippedExpiry(history.expiry, "no exit rules for this entry")
        stop, target, trail = policy.stop_loss, policy.target_profit, policy.trailing_stop

    close_dt = datetime.combine(history.expiry, time(15, 30), tzinfo=_IST)
    square_off = close_dt - timedelta(minutes=config.square_off_minutes_before_close)
    keys = [(strike, kind) for strike, kind, *_ in entries]
    moments = sorted(
        moment for moment in set.intersection(*(set(closes[key]) for key in keys))
        if entry_at < moment <= square_off
    )
    if not moments or moments[-1].date() != history.expiry:
        return SkippedExpiry(history.expiry, "no common bar on expiry day before square-off")

    # Every rule set reads the same prices, so each moment is only priced once.
    @cache
    def exit_at_moment(moment: datetime) -> tuple[Decimal, tuple[Decimal, ...]]:
        cash, prices = Decimal(0), []
        for strike, kind, side, units, _ in entries:
            exit_side: Literal["BUY", "SELL"] = "SELL" if side == "BUY" else "BUY"
            close = closes[(strike, kind)][moment]
            price = fill_price(close, exit_side, config.half_spread_for(close))
            prices.append(price)
            cash += price * units if exit_side == "SELL" else -price * units
        return cash, tuple(prices)

    try:
        schedule_for(entry_at.date())
    except ValueError:
        # Costs before the first dated schedule are unknown; skip rather than guess them.
        return SkippedExpiry(history.expiry, "no charge schedule covers the entry date")

    def charges_for(prices: Sequence[Decimal], exit_day: date) -> Decimal:
        total = Decimal(0)
        for (_, _, side, units, price), exit_price in zip(entries, prices, strict=True):
            exit_side: Literal["BUY", "SELL"] = "SELL" if side == "BUY" else "BUY"
            total += order_charges(instrument="OPT", side=side, price=price, quantity=units,
                                   trade_date=entry_at.date()).total
            total += order_charges(instrument="OPT", side=exit_side, price=exit_price,
                                   quantity=units, trade_date=exit_day).total
        return total

    def close_trade(
        stop: Decimal | None, target: Decimal | None, trail: Decimal | None,
        checks: list[datetime], last_reason: ExitReason,
    ) -> BacktestTrade:
        reason, exit_moment = last_reason, checks[-1]
        peak = Decimal(0)
        for moment in checks:
            decision = evaluate_exit(
                pnl=entry_cash + exit_at_moment(moment)[0], peak_pnl=peak,
                stop_loss_amount=stop, target_profit_amount=target, trailing_stop_amount=trail,
            )
            peak = decision.peak_pnl
            if decision.reason is not None:
                reason, exit_moment = cast(ExitReason, decision.reason), moment
                break
        exit_cash, exit_prices = exit_at_moment(exit_moment)
        return BacktestTrade(
            expiry=history.expiry, entry_at=entry_at, exit_at=exit_moment, exit_reason=reason,
            synthetic_spot=_money(spot), reference_strike=reference, max_loss=max_loss,
            gross_pnl=_money(entry_cash + exit_cash),
            charges=_money(charges_for(exit_prices, exit_moment.date())),
            legs=[
                BacktestLeg(strike=strike, option_type=kind, side=side, quantity_units=units,
                            entry_price=price, exit_price=exit_price)
                for (strike, kind, side, units, price), exit_price
                in zip(entries, exit_prices, strict=True)
            ],
            entry_margin=entry_margin,
        )

    entry_margin = _entry_margin(history, config, entry_at, spot, margin_inputs)
    trade = close_trade(stop, target, trail, moments, "EXPIRY_SQUARE_OFF")
    for rules in config.exit_rule_sets:
        new_target, new_trail = target, trail
        if rules.target_multiple is None or target is None:
            new_target, new_trail = None, None
        else:
            new_target = target * rules.target_multiple
            if rules.charges_floor_multiple is not None:
                entry_prices = [price for *_, price in entries]
                new_target = max(new_target, rules.charges_floor_multiple
                                 * charges_for(entry_prices, entry_at.date()))
            new_trail = _money(trail * new_target / target) if trail is not None else None
            new_target = _money(new_target)
        checks = moments
        last_reason: ExitReason = "EXPIRY_SQUARE_OFF"
        if rules.leave_before_expiry_day:
            early = _before_expiry_day(moments, history.expiry)
            if early:
                checks, last_reason = early, "EARLY_SQUARE_OFF"
        trade.alternatives[rules.name] = close_trade(
            stop if rules.use_stop else None, new_target,
            new_trail if rules.use_trail else None, checks, last_reason,
        )
    return trade


def _before_expiry_day(moments: list[datetime], expiry: date) -> list[datetime]:
    """Moments up to 15:10 on the last earlier session; empty when entry was on expiry day."""
    days = [moment.date() for moment in moments if moment.date() < expiry]
    if not days:
        return []
    cutoff = datetime.combine(max(days), time(15, 10), tzinfo=_IST)
    return [moment for moment in moments if moment <= cutoff]


def run_backtest(histories: Sequence[ExpiryHistory], config: BacktestConfig) -> BacktestReport:
    if config.lots < 1:
        raise ValueError("Backtest needs at least one lot")
    report = BacktestReport(config=config)
    for history in sorted(histories, key=lambda item: item.expiry):
        outcome = run_expiry(history, config)
        if isinstance(outcome, SkippedExpiry):
            report.skipped.append(outcome)
        else:
            report.trades.append(outcome)
    return report
