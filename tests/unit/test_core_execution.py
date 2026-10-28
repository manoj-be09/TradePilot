from datetime import datetime, timedelta, timezone
from decimal import Decimal

from packages.core.execution import (
    BookLevel,
    BookSnapshot,
    ExecutionConfig,
    OrderLeg,
    book_from_depth,
    simulate,
)

NOW = datetime(2026, 10, 28, 5, 0, tzinfo=timezone.utc)


def _book(bids: list[tuple[str, int]], asks: list[tuple[str, int]], age: float = 1) -> BookSnapshot:
    return BookSnapshot(
        NOW - timedelta(seconds=age),
        tuple(BookLevel(Decimal(p), q) for p, q in bids),
        tuple(BookLevel(Decimal(p), q) for p, q in asks),
    )


BOOKS = {
    "CE": _book([("100.00", 130), ("99.95", 260)], [("100.10", 65), ("100.20", 195)]),
    "PE": _book([("80.00", 650)], [("80.10", 650)]),
}


def test_buy_walks_asks_and_sell_hits_bids() -> None:
    result = simulate([OrderLeg("CE", "BUY", 130)], BOOKS, NOW)
    assert result.status == "FILLED"
    (leg,) = result.legs
    assert leg.average_price == Decimal("100.1500") and leg.levels_used == 2
    sell = simulate([OrderLeg("CE", "SELL", 65)], BOOKS, NOW).legs[0]
    assert sell.average_price == Decimal("100.0000")


def test_later_legs_pay_leg_risk_ticks() -> None:
    result = simulate([OrderLeg("CE", "SELL", 65), OrderLeg("PE", "BUY", 65)], BOOKS, NOW)
    assert result.status == "FILLED"
    assert result.legs[1].average_price == Decimal("80.1500")  # 80.10 + one 0.05 tick


def test_insufficient_size_rejects_whole_structure_by_default() -> None:
    legs = [OrderLeg("PE", "SELL", 65), OrderLeg("CE", "BUY", 1000)]
    result = simulate(legs, BOOKS, NOW)
    assert result.status == "REJECTED"
    assert all(leg.filled == 0 for leg in result.legs)
    assert "displayed size covers 260 of 1000" in result.reasons[0]
    partial = simulate(legs, BOOKS, NOW, ExecutionConfig(allow_partial=True))
    assert partial.status == "PARTIAL" and partial.legs[1].filled == 260


def test_stale_crossed_and_missing_books_are_rejected() -> None:
    books = {
        "OLD": _book([("1", 100)], [("1.1", 100)], age=30),
        "X": _book([("2.0", 100)], [("1.9", 100)]),
    }
    reasons = simulate(
        [OrderLeg("OLD", "BUY", 1), OrderLeg("X", "BUY", 1), OrderLeg("NONE", "BUY", 1)],
        books, NOW,
    ).reasons
    assert "stale" in reasons[0] and "crossed" in reasons[1] and "no book" in reasons[2]


def test_book_from_recorder_depth_skips_empty_levels() -> None:
    book = book_from_depth([[650, "100.0", 325, "100.1"], [0, "0.0", 130, "100.2"]], NOW)
    assert book.bids == (BookLevel(Decimal("100.0"), 650),)
    assert [level.price for level in book.asks] == [Decimal("100.1"), Decimal("100.2")]
