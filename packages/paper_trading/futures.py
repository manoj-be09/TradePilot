"""Pure NIFTY futures paper rules: quote eligibility, margin, P&L and reporting charges.

Margin here is a flat illustrative fraction of notional, not an exchange SPAN figure.
It only decides how much simulated cash is blocked; it never reaches a broker.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from packages.core.charges import order_charges
from packages.market_data.contracts import Quote
from packages.paper_trading.quotes import PaperQuoteError, require_fresh

FuturesSide = Literal["LONG", "SHORT"]
OrderSide = Literal["BUY", "SELL"]

MARGIN_MODEL_ID = "nifty-futures-flat-notional-pct-v1"
MARGIN_DISCLAIMER = (
    "Illustrative margin: a flat percentage of entry notional. Not NSE SPAN plus exposure "
    "margin and not a broker requirement; real margin can be higher, especially near expiry "
    "or in volatile sessions."
)
GAP_RISK_NOTE = (
    "The stop-loss amount is the risk budget used for account caps. A price gap through "
    "the stop can realize a larger loss."
)
MAX_FUTURES_SPREAD = Decimal("0.005")
_CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def entry_order_side(side: FuturesSide) -> OrderSide:
    return "BUY" if side == "LONG" else "SELL"


def exit_order_side(side: FuturesSide) -> OrderSide:
    return "SELL" if side == "LONG" else "BUY"


def estimate_margin(price: Decimal, quantity_units: int, margin_pct: Decimal) -> Decimal:
    if price <= 0 or quantity_units <= 0 or margin_pct <= 0:
        raise ValueError("Margin needs a positive price, quantity and percentage")
    return money(price * quantity_units * margin_pct / Decimal(100))


def futures_pnl(
    side: FuturesSide, entry_price: Decimal, exit_price: Decimal, quantity_units: int
) -> Decimal:
    direction = Decimal(1) if side == "LONG" else Decimal(-1)
    return money((exit_price - entry_price) * quantity_units * direction)


def require_futures_quote(
    quote: Quote,
    order_side: OrderSide,
    quantity_units: int,
    *,
    max_age_seconds: int,
    allow_unverified: bool,
    now: datetime | None = None,
) -> Decimal:
    """Return the executable side of a fresh, tight, deep-enough futures quote."""
    checked_at = now or datetime.now(timezone.utc)
    if quote.timestamp_basis != "exchange" and not allow_unverified:
        raise PaperQuoteError(
            "Futures quote freshness is not exchange-verified; paper execution is blocked."
        )
    require_fresh(quote.timestamp, now=checked_at, max_age_seconds=max_age_seconds)
    if (quote.book_timestamp_basis != "exchange" or quote.book_as_of is None) and (
        not allow_unverified
    ):
        raise PaperQuoteError(
            "Futures bid/ask book update time is unverified; paper execution is blocked."
        )
    if quote.book_as_of is not None:
        require_fresh(quote.book_as_of, now=checked_at, max_age_seconds=max_age_seconds)
    bid, ask = quote.bid, quote.ask
    if bid is None or ask is None or bid <= 0 or ask <= 0 or bid > ask:
        raise PaperQuoteError("Futures contract has no usable two-sided quote")
    if (ask - bid) / ((ask + bid) / Decimal(2)) > MAX_FUTURES_SPREAD:
        raise PaperQuoteError("Futures spread is too wide for a paper fill")
    available = quote.ask_quantity if order_side == "BUY" else quote.bid_quantity
    if available is None or available < quantity_units:
        raise PaperQuoteError("Displayed futures quote size is insufficient for this order")
    return ask if order_side == "BUY" else bid


def quote_evidence(quote: Quote) -> dict[str, Any]:
    return {
        "instrument_key": quote.instrument_token,
        "bid": str(quote.bid) if quote.bid is not None else None,
        "ask": str(quote.ask) if quote.ask is not None else None,
        "bid_quantity": quote.bid_quantity,
        "ask_quantity": quote.ask_quantity,
        "last_price": str(quote.last_price),
        "quote_timestamp": quote.timestamp.isoformat(),
        "timestamp_basis": quote.timestamp_basis,
        "book_as_of": quote.book_as_of.isoformat() if quote.book_as_of else None,
        "book_timestamp_basis": quote.book_timestamp_basis,
        "last_trade_time": quote.last_trade_time.isoformat() if quote.last_trade_time else None,
        "volume": quote.volume,
    }


def charge_snapshot(
    order_side: OrderSide, price: Decimal, quantity_units: int, trade_date: date
) -> dict[str, Any]:
    """Reporting-only charges for one futures order; cash flows exclude them."""
    charges = order_charges(
        instrument="FUT", side=order_side, price=price, quantity=quantity_units,
        trade_date=trade_date,
    )
    return {
        "schedule_id": charges.schedule_id,
        "plan_id": charges.plan_id,
        "verified": charges.verified,
        "turnover": str(charges.turnover),
        "total_charge": str(money(charges.total)),
        "reporting_only": True,
    }


@dataclass(frozen=True)
class FuturesMark:
    price: Decimal
    pnl: Decimal
    close_cash_flow: Decimal


def mark_position(
    *,
    side: FuturesSide,
    entry_price: Decimal,
    exit_price: Decimal,
    quantity_units: int,
    margin_blocked: Decimal,
) -> FuturesMark:
    """Closing releases the blocked margin plus the price P&L in one cash flow."""
    pnl = futures_pnl(side, entry_price, exit_price, quantity_units)
    return FuturesMark(exit_price, pnl, money(margin_blocked + pnl))
