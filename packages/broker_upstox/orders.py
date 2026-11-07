"""Order models and the live order state machine (LIVE_TRADING.md section 2).

`UNKNOWN` (a send that timed out) is resolved only from the broker's order book, never by
sending again. Limit orders only; market orders do not exist in this model.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

Side = Literal["BUY", "SELL"]
TICK_SIZE = Decimal("0.05")
TAG_MAX_LENGTH = 40  # Upstox caps an order tag at 40 characters.


class OrderState(StrEnum):
    NEW = "NEW"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


TERMINAL = frozenset({OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED})
_BROKER_OUTCOMES = frozenset({OrderState.ACKNOWLEDGED, OrderState.PARTIAL, *TERMINAL})
_ALLOWED: dict[OrderState, frozenset[OrderState]] = {
    OrderState.NEW: frozenset({OrderState.SENT}),
    OrderState.SENT: _BROKER_OUTCOMES | {OrderState.UNKNOWN},
    OrderState.UNKNOWN: _BROKER_OUTCOMES,
    OrderState.ACKNOWLEDGED: frozenset({OrderState.PARTIAL, *TERMINAL}),
    OrderState.PARTIAL: frozenset({OrderState.PARTIAL, OrderState.FILLED, OrderState.CANCELLED}),
}


class IllegalOrderTransition(ValueError):
    pass


def advance(current: OrderState, new: OrderState) -> OrderState:
    """The next state, or IllegalOrderTransition. A repeated non-terminal update is a no-op."""
    if new == current and current not in TERMINAL:
        return current
    if new not in _ALLOWED.get(current, frozenset()):
        raise IllegalOrderTransition(f"{current} -> {new} is not a legal order transition")
    return new


# Upstox order-book statuses that mean the broker holds a live, unfilled-or-partial order.
_WORKING = frozenset({
    "put order req received", "validation pending", "open pending", "open", "trigger pending",
    "modify validation pending", "modify pending", "modified", "not modified", "cancel pending",
    "not cancelled", "after market order req received",
})


def state_from_broker(status: str, quantity: int, filled_quantity: int) -> OrderState:
    """Map one Upstox order-book row to our state. Anything unexpected is UNKNOWN (fail closed)."""
    status = status.strip().lower()
    if not 0 <= filled_quantity <= quantity:
        return OrderState.UNKNOWN
    if status in _WORKING:
        return OrderState.PARTIAL if filled_quantity else OrderState.ACKNOWLEDGED
    if status == "complete":
        return OrderState.FILLED if filled_quantity == quantity else OrderState.UNKNOWN
    if status in ("cancelled", "cancelled after market order"):
        return OrderState.CANCELLED  # any partial fill stays in filled_quantity
    if status == "rejected":
        return OrderState.REJECTED if filled_quantity == 0 else OrderState.UNKNOWN
    return OrderState.UNKNOWN


def client_tag(deployment_id: UUID, leg: int, attempt: int) -> str:
    """Deterministic order tag, so a restart can find its own orders before acting."""
    tag = f"tp-{deployment_id.hex[:16]}-{leg}-{attempt}"
    if len(tag) > TAG_MAX_LENGTH:
        raise ValueError("Order tag is longer than Upstox allows")
    return tag


@dataclass(frozen=True)
class LiveOrderRequest:
    instrument_key: str
    side: Side
    quantity: int
    limit_price: Decimal
    tag: str
    product: Literal["D"] = "D"  # carry-forward NRML; intraday products are not offered
    validity: Literal["DAY"] = "DAY"


def hedge_first(legs: list[LiveOrderRequest]) -> list[LiveOrderRequest]:
    """Buy legs before sell legs, so a partial failure never leaves naked short exposure."""
    return sorted(legs, key=lambda leg: leg.side != "BUY")


@dataclass(frozen=True)
class FilledLeg:
    instrument_key: str
    side: Side
    filled_quantity: int
    reference_price: Decimal  # fresh quote on the closing side, used for the limit price


def flatten_plan(filled: list[FilledLeg], *, deployment_id: UUID, attempt: int,
                 slippage_pct: Decimal) -> list[LiveOrderRequest]:
    """Closing orders for legs that did fill, shorts bought back first to stay hedged."""
    orders = []
    for leg_number, leg in enumerate(filled):
        if leg.filled_quantity <= 0:
            continue
        side: Side = "SELL" if leg.side == "BUY" else "BUY"
        factor = 1 + slippage_pct / 100 if side == "BUY" else 1 - slippage_pct / 100
        orders.append(LiveOrderRequest(
            instrument_key=leg.instrument_key, side=side, quantity=leg.filled_quantity,
            limit_price=round_to_tick(leg.reference_price * factor, side),
            tag=client_tag(deployment_id, leg_number, attempt),
        ))
    return hedge_first(orders)


def round_to_tick(price: Decimal, side: Side) -> Decimal:
    """Round to the 0.05 tick in the direction that keeps the limit marketable."""
    ticks = price / TICK_SIZE
    whole = ticks.to_integral_value(rounding="ROUND_CEILING" if side == "BUY" else "ROUND_FLOOR")
    return max(TICK_SIZE, whole * TICK_SIZE)
