"""Replay a registered option strategy over imported historical NIFTY option candles.

Candles carry no bid/ask, so every fill is the bar close moved against us by a labelled
half-spread model. Exits are checked on bar closes only, so a move inside a bar can
realize more than the stop (gap risk). An expiry whose legs lack a common bar at entry,
or that has no bar before the expiry-day square-off, is skipped and reported, never
filled in. Spot comes from put-call parity at the strike where call and put trade
closest, because the import stores option candles only.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from packages.backtesting.performance import PerformanceTrade, summarize
from packages.backtesting.spread_model import SpreadModel
from packages.core.charges import order_charges
from packages.options.models import PayoffLeg, PayoffRequest
from packages.options.payoff import calculate_expiry_payoff
from packages.paper_trading.strategies import StrategyDefinition

MODEL_ID = "nifty-history-candle-replay-v1"
FILL_MODEL = (
    "Fills at the bar close moved against the trade by the half-spread model (flat, or "
    "per premium band from recorded quotes); no "
    "historical bid/ask, queue or depth. Exits are checked on bar closes only."
)
_IST = ZoneInfo("Asia/Kolkata")
_CENT = Decimal("0.01")
_TICK = Decimal("0.05")
ExitReason = Literal["STOP_LOSS", "TARGET", "EXPIRY_SQUARE_OFF"]
OptionKind = Literal["CE", "PE"]


@dataclass(frozen=True)
class Candle:
    start: datetime
    close: Decimal


@dataclass(frozen=True)
class ExpiryHistory:
    """Bars per (strike, option type) for one expiry, each list ordered by time."""

    expiry: date
    lot_size: int
    bars: dict[tuple[Decimal, OptionKind], list[Candle]]


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

    def half_spread_for(self, price: Decimal) -> Decimal:
        if self.spread_model is None:
            return self.half_spread_pct
        return self.spread_model.half_spread_pct(price)

    def snapshot(self) -> dict[str, Any]:
        return {
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

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.charges


@dataclass(frozen=True)
class SkippedExpiry:
    expiry: date
    reason: str


@dataclass
class BacktestReport:
    config: BacktestConfig
    trades: list[BacktestTrade] = field(default_factory=list)
    skipped: list[SkippedExpiry] = field(default_factory=list)

    def summary(self, folds: int = 4) -> dict[str, Any]:
        ordered = sorted(self.trades, key=lambda trade: trade.exit_at)
        return {
            "model_id": MODEL_ID,
            "fill_model": FILL_MODEL,
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
    result["cvar_5pct"] = (
        float(value) if (value := _cvar([trade.net_pnl for trade in trades])) is not None
        else None
    )
    result["exit_reasons"] = {
        reason: sum(trade.exit_reason == reason for trade in trades)
        for reason in ("STOP_LOSS", "TARGET", "EXPIRY_SQUARE_OFF")
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
    entry_cash = Decimal(0)
    payoff_legs = []
    for strike, kind, side, ratio in legs:
        units = ratio * config.lots * history.lot_size
        close = closes[(strike, kind)][entry_at]
        price = fill_price(close, side, config.half_spread_for(close))
        entry_cash += -price * units if side == "BUY" else price * units
        entries.append((strike, kind, side, units, price))
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

    close_dt = datetime.combine(history.expiry, time(15, 30), tzinfo=_IST)
    square_off = close_dt - timedelta(minutes=config.square_off_minutes_before_close)
    keys = [(strike, kind) for strike, kind, *_ in entries]
    moments = sorted(
        moment for moment in set.intersection(*(set(closes[key]) for key in keys))
        if entry_at < moment <= square_off
    )
    if not moments or moments[-1].date() != history.expiry:
        return SkippedExpiry(history.expiry, "no common bar on expiry day before square-off")

    def exit_at_moment(moment: datetime) -> tuple[Decimal, list[Decimal]]:
        cash, prices = Decimal(0), []
        for strike, kind, side, units, _ in entries:
            exit_side: Literal["BUY", "SELL"] = "SELL" if side == "BUY" else "BUY"
            close = closes[(strike, kind)][moment]
            price = fill_price(close, exit_side, config.half_spread_for(close))
            prices.append(price)
            cash += price * units if exit_side == "SELL" else -price * units
        return cash, prices

    reason: ExitReason = "EXPIRY_SQUARE_OFF"
    exit_moment = moments[-1]
    for moment in moments:
        pnl = entry_cash + exit_at_moment(moment)[0]
        if config.stop_loss_amount is not None and pnl <= -config.stop_loss_amount:
            reason, exit_moment = "STOP_LOSS", moment
            break
        if config.target_profit_amount is not None and pnl >= config.target_profit_amount:
            reason, exit_moment = "TARGET", moment
            break
    exit_cash, exit_prices = exit_at_moment(exit_moment)

    charges = Decimal(0)
    for (_, _, side, units, price), exit_price in zip(entries, exit_prices, strict=True):
        exit_side: Literal["BUY", "SELL"] = "SELL" if side == "BUY" else "BUY"
        charges += order_charges(instrument="OPT", side=side, price=price, quantity=units,
                                 trade_date=entry_at.date()).total
        charges += order_charges(instrument="OPT", side=exit_side, price=exit_price,
                                 quantity=units, trade_date=exit_moment.date()).total
    return BacktestTrade(
        expiry=history.expiry, entry_at=entry_at, exit_at=exit_moment, exit_reason=reason,
        synthetic_spot=_money(spot), reference_strike=reference, max_loss=max_loss,
        gross_pnl=_money(entry_cash + exit_cash), charges=_money(charges),
        legs=[
            BacktestLeg(strike=strike, option_type=kind, side=side, quantity_units=units,
                        entry_price=price, exit_price=exit_price)
            for (strike, kind, side, units, price), exit_price
            in zip(entries, exit_prices, strict=True)
        ],
    )


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
