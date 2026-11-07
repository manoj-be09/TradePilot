"""The live order ledger: every order and state change, kept apart from paper, with alerts.

Each change goes through the state machine in `orders.py`, so an illegal update raises and
is never stored. Every change notifies the release owner; the alert relay forwards it.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.broker_upstox.orders import LiveOrderRequest, OrderState, advance, state_from_broker
from packages.database.clock import monotonic_utc_now
from packages.database.models import (
    LiveOrder,
    LiveOrderEvent,
    LiveRelease,
    UserNotification,
)


def record_order(session: Session, release_id: UUID, request: LiveOrderRequest) -> LiveOrder:
    """Store a NEW order before anything is sent; the unique tag stops a second copy."""
    order = LiveOrder(release_id=release_id, tag=request.tag,
                      instrument_key=request.instrument_key, side=request.side,
                      quantity=request.quantity, limit_price=request.limit_price,
                      state=OrderState.NEW, filled_quantity=0)
    session.add(order)
    session.flush()
    _event(session, order, None)
    session.commit()
    return order


def move(session: Session, order: LiveOrder, new: OrderState, *, broker_status: str | None = None,
         filled_quantity: int | None = None, average_price: Decimal | None = None,
         broker_order_id: str | None = None) -> None:
    previous = OrderState(order.state)
    order.state = advance(previous, new)
    if filled_quantity is not None:
        if filled_quantity < order.filled_quantity:
            raise ValueError("Filled quantity cannot go down")
        order.filled_quantity = filled_quantity
    if average_price is not None:
        order.average_price = average_price
    if broker_order_id is not None:
        order.broker_order_id = broker_order_id
    order.updated_at = monotonic_utc_now()
    if order.state != previous or filled_quantity is not None:
        _event(session, order, broker_status)
    if order.state != previous:
        _alert(session, order)
    session.commit()


def apply_broker_row(session: Session, order: LiveOrder, *, status: str, filled_quantity: int,
                     average_price: Decimal | None, broker_order_id: str) -> None:
    """Apply one order-book row (how an UNKNOWN order is resolved)."""
    state = state_from_broker(status, order.quantity, filled_quantity)
    move(session, order, state, broker_status=status[:64], filled_quantity=filled_quantity,
         average_price=average_price, broker_order_id=broker_order_id)


def expected_positions(session: Session) -> dict[str, int]:
    """Net filled quantity per instrument from the ledger, for reconciliation."""
    out: dict[str, int] = {}
    for key, side, filled in session.execute(
        select(LiveOrder.instrument_key, LiveOrder.side, LiveOrder.filled_quantity)
    ):
        out[key] = out.get(key, 0) + (filled if side == "BUY" else -filled)
    return {key: quantity for key, quantity in out.items() if quantity}


def _event(session: Session, order: LiveOrder, broker_status: str | None) -> None:
    session.add(LiveOrderEvent(order_id=order.id, state=order.state,
                               filled_quantity=order.filled_quantity,
                               broker_status=broker_status))


def _alert(session: Session, order: LiveOrder) -> None:
    owner = session.scalar(select(LiveRelease.owner_user_id)
                           .where(LiveRelease.id == order.release_id))
    if owner is None:
        return
    session.add(UserNotification(
        user_id=owner, event_type=f"live.order.{order.state.lower()}",
        title=f"Live order {order.state.lower()}"[:160],
        message=(f"{order.side} {order.quantity} {order.instrument_key} at {order.limit_price}: "
                 f"{order.state}, filled {order.filled_quantity}")[:512],
        details={"tag": order.tag, "state": order.state},
    ))
