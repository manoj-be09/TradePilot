"""Platform pre-trade controls for a live entry (LIVE_TRADING.md section 4).

Every check runs and every failure is returned; the first is the one to report. An empty
result is necessary for a send, never sufficient: the send path (not built) also needs its
three locks. The account limits are the paper ones from `packages.core.risk`, measured on
live capital.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from packages.broker_upstox.orders import TICK_SIZE, LiveOrderRequest
from packages.core.risk import (
    AccountRiskState,
    EntryIntent,
    RiskLimits,
    RiskRejection,
    evaluate_entry,
)


@dataclass(frozen=True)
class LiveLimits:
    account: RiskLimits
    max_lots_per_order: int
    max_lots_per_day: int
    max_orders_per_minute: int
    freeze_quantity: int  # NSE freeze limit for the contract, in units
    max_slippage_pct: Decimal  # how far a limit may sit beyond the touch
    max_quote_age: timedelta


@dataclass(frozen=True)
class LegQuote:
    bid: Decimal
    ask: Decimal
    exchange_time: datetime | None  # None: the quote has no exchange timestamp


@dataclass(frozen=True)
class LiveEntryContext:
    halted: bool
    account: AccountRiskState  # realized_pnl_today must include today's unrealized P&L
    lot_size: int
    quotes: dict[str, LegQuote]
    now: datetime
    orders_last_minute: int
    lots_today: int
    margin_required: Decimal | None  # Upstox margin for the basket; None = not obtained
    available_funds: Decimal | None


def _no(code: str, message: str) -> RiskRejection:
    return RiskRejection(code, 409, message)


def check_live_entry(legs: list[LiveOrderRequest], intent: EntryIntent,
                     context: LiveEntryContext, limits: LiveLimits) -> tuple[RiskRejection, ...]:
    out: list[RiskRejection] = []
    if context.halted:
        out.append(RiskRejection("LIVE_HALTED", 423, "Live trading is halted"))
    if not legs:
        out.append(_no("NO_LEGS", "An entry needs at least one leg"))
    out.extend(evaluate_entry(intent, context.account, limits.account).rejections)

    lots = 0
    for leg in legs:
        name = leg.instrument_key
        if leg.quantity <= 0 or leg.quantity % context.lot_size:
            out.append(_no("LOT_MULTIPLE", f"{name}: quantity is not a whole number of lots"))
        leg_lots = leg.quantity // context.lot_size
        lots = max(lots, leg_lots)
        if leg_lots > limits.max_lots_per_order:
            out.append(_no("MAX_LOTS_PER_ORDER", f"{name}: {leg_lots} lots is over the cap"))
        if leg.quantity > limits.freeze_quantity:
            out.append(_no("FREEZE_QUANTITY", f"{name}: quantity is over the NSE freeze limit"))
        if leg.limit_price <= 0 or leg.limit_price % TICK_SIZE:
            out.append(_no("TICK_SIZE", f"{name}: limit price is not on the 0.05 tick"))
        out.extend(_quote_checks(leg, context.quotes.get(name), context.now, limits))

    if context.lots_today + lots > limits.max_lots_per_day:
        out.append(_no("MAX_LOTS_PER_DAY", "Daily live lot cap would be exceeded"))
    if context.orders_last_minute + len(legs) > limits.max_orders_per_minute:
        out.append(_no("ORDER_RATE", "Orders-per-minute cap would be exceeded"))
    if context.margin_required is None or context.available_funds is None:
        out.append(_no("MARGIN_UNKNOWN", "Broker margin or available funds could not be read"))
    elif context.margin_required > context.available_funds:
        out.append(_no("MARGIN", "Broker margin for the basket exceeds available funds"))
    return tuple(out)


def _quote_checks(leg: LiveOrderRequest, quote: LegQuote | None, now: datetime,
                  limits: LiveLimits) -> list[RiskRejection]:
    name = leg.instrument_key
    if quote is None or quote.bid <= 0 or quote.ask <= 0 or quote.bid > quote.ask:
        return [_no("QUOTE_INVALID", f"{name}: no usable two-sided quote")]
    if quote.exchange_time is None or now - quote.exchange_time > limits.max_quote_age:
        return [_no("QUOTE_STALE", f"{name}: quote has no fresh exchange timestamp")]
    allowance = 1 + limits.max_slippage_pct / 100
    if leg.side == "BUY" and leg.limit_price > quote.ask * allowance:
        return [_no("LIMIT_PRICE", f"{name}: buy limit is too far above the ask")]
    if leg.side == "SELL" and leg.limit_price < quote.bid / allowance:
        return [_no("LIMIT_PRICE", f"{name}: sell limit is too far below the bid")]
    return []
