"""Depth-limited simulated execution for multi-leg F&O orders (model v1).

Conservative by construction: a BUY consumes displayed asks and a SELL consumes displayed
bids, level by level, never more than displayed size; each later leg pays extra adverse
ticks for leg risk; stale or crossed books are rejected. A simulated fill is never
exchange evidence and never sent anywhere.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

MODEL_ID = "depth-walk-v1"
Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class BookLevel:
    price: Decimal
    quantity: int


@dataclass(frozen=True)
class BookSnapshot:
    as_of: datetime
    bids: tuple[BookLevel, ...]  # best first
    asks: tuple[BookLevel, ...]  # best first


@dataclass(frozen=True)
class OrderLeg:
    instrument_key: str
    side: Side
    quantity: int  # units (lots × lot size)


@dataclass(frozen=True)
class ExecutionConfig:
    tick_size: Decimal = Decimal("0.05")
    max_book_age_seconds: float = 5.0
    leg_slippage_ticks: int = 1  # extra adverse ticks for each subsequent leg
    allow_partial: bool = False


DEFAULT_CONFIG = ExecutionConfig()


@dataclass(frozen=True)
class LegFill:
    instrument_key: str
    side: Side
    requested: int
    filled: int
    average_price: Decimal | None
    levels_used: int


@dataclass(frozen=True)
class ExecutionResult:
    model_id: str
    status: Literal["FILLED", "PARTIAL", "REJECTED"]
    legs: tuple[LegFill, ...]
    reasons: tuple[str, ...]


def book_from_depth(levels: Sequence[Sequence[object]], as_of: datetime) -> BookSnapshot:
    """Build a book from recorder depth rows `[bid_qty, bid_price, ask_qty, ask_price]`."""
    bids: list[BookLevel] = []
    asks: list[BookLevel] = []
    for bid_qty, bid_price, ask_qty, ask_price in levels:
        bp, ap = Decimal(str(bid_price)), Decimal(str(ask_price))
        bq, aq = int(str(bid_qty)), int(str(ask_qty))
        if bp > 0 and bq > 0:
            bids.append(BookLevel(bp, bq))
        if ap > 0 and aq > 0:
            asks.append(BookLevel(ap, aq))
    return BookSnapshot(as_of, tuple(bids), tuple(asks))


def simulate(
    legs: Sequence[OrderLeg],
    books: Mapping[str, BookSnapshot],
    now: datetime,
    config: ExecutionConfig = DEFAULT_CONFIG,
) -> ExecutionResult:
    if not legs:
        return ExecutionResult(MODEL_ID, "REJECTED", (), ("No legs to execute",))
    reasons: list[str] = []
    fills: list[LegFill] = []
    for index, leg in enumerate(legs):
        if leg.quantity <= 0:
            reasons.append(f"{leg.instrument_key}: quantity must be positive")
            fills.append(LegFill(leg.instrument_key, leg.side, leg.quantity, 0, None, 0))
            continue
        book = books.get(leg.instrument_key)
        problem = _book_problem(book, now, config)
        if book is None or problem is not None:
            reasons.append(f"{leg.instrument_key}: {problem or 'no book'}")
            fills.append(LegFill(leg.instrument_key, leg.side, leg.quantity, 0, None, 0))
            continue
        side_levels = book.asks if leg.side == "BUY" else book.bids
        adverse = config.tick_size * config.leg_slippage_ticks * index
        remaining, cost, used = leg.quantity, Decimal(0), 0
        for level in side_levels:
            if remaining == 0:
                break
            take = min(remaining, level.quantity)
            price = level.price + adverse if leg.side == "BUY" else level.price - adverse
            if price <= 0:
                break
            cost += price * take
            remaining -= take
            used += 1
        filled = leg.quantity - remaining
        average = (cost / filled).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) \
            if filled else None
        if remaining:
            reasons.append(
                f"{leg.instrument_key}: displayed size covers {filled} of {leg.quantity}"
            )
        fills.append(LegFill(leg.instrument_key, leg.side, leg.quantity, filled, average, used))

    complete = all(fill.filled == fill.requested for fill in fills)
    if complete:
        return ExecutionResult(MODEL_ID, "FILLED", tuple(fills), ())
    if config.allow_partial and any(fill.filled for fill in fills):
        return ExecutionResult(MODEL_ID, "PARTIAL", tuple(fills), tuple(reasons))
    rejected = tuple(
        LegFill(f.instrument_key, f.side, f.requested, 0, None, 0) for f in fills
    )
    return ExecutionResult(MODEL_ID, "REJECTED", rejected, tuple(reasons))


def _book_problem(
    book: BookSnapshot | None, now: datetime, config: ExecutionConfig
) -> str | None:
    if book is None:
        return "no book"
    age = (now - book.as_of).total_seconds()
    if age > config.max_book_age_seconds:
        return f"book is stale ({age:.1f}s old)"
    if age < -5:
        return "book time is in the future"
    if book.bids and book.asks and book.bids[0].price >= book.asks[0].price:
        return "book is crossed"
    return None
