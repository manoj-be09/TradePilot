from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from packages.database.futures_models import PaperFuturesOrder, PaperFuturesTrade
from packages.database.models import PaperAccount
from packages.market_data.contracts import Quote
from packages.paper_trading.futures import (
    estimate_margin,
    futures_pnl,
    mark_position,
    require_futures_quote,
)
from packages.paper_trading.ledger_reconciliation import reconcile_paper_ledger
from packages.paper_trading.quotes import PaperQuoteError

NOW = datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)


def _quote(**overrides) -> Quote:
    values = {
        "instrument_token": "NSE_FO|FUT1", "timestamp": NOW, "timestamp_basis": "exchange",
        "book_as_of": NOW, "book_timestamp_basis": "exchange", "last_price": Decimal("25010"),
        "bid": Decimal("25000"), "ask": Decimal("25002"), "bid_quantity": 750,
        "ask_quantity": 750,
    }
    values.update(overrides)
    return Quote(**values)


def _check(quote: Quote, side="BUY", units=75, allow_unverified=False) -> Decimal:
    return require_futures_quote(
        quote, side, units, max_age_seconds=60, allow_unverified=allow_unverified, now=NOW,
    )


def test_buys_lift_the_ask_and_sells_hit_the_bid():
    assert _check(_quote(), "BUY") == Decimal("25002")
    assert _check(_quote(), "SELL") == Decimal("25000")


@pytest.mark.parametrize("overrides, message", [
    ({"timestamp_basis": "provider_response"}, "not exchange-verified"),
    ({"book_timestamp_basis": "unknown"}, "book update time"),
    ({"timestamp": NOW - timedelta(minutes=5)}, "stale"),
    ({"bid": None}, "two-sided"),
    ({"bid": Decimal("24800")}, "spread"),
    ({"ask_quantity": 50}, "size"),
])
def test_unsafe_quotes_are_rejected(overrides, message):
    with pytest.raises(PaperQuoteError, match=message):
        _check(_quote(**overrides))


def test_unverified_override_still_checks_freshness_and_depth():
    unverified = _quote(timestamp_basis="provider_response", book_timestamp_basis="unknown",
                        book_as_of=None)
    assert _check(unverified, allow_unverified=True) == Decimal("25002")
    with pytest.raises(PaperQuoteError, match="size"):
        _check(_quote(timestamp_basis="provider_response", bid_quantity=10), "SELL",
               allow_unverified=True)


def test_margin_and_pnl_follow_position_direction():
    assert estimate_margin(Decimal("25000"), 75, Decimal(12)) == Decimal("225000.00")
    assert futures_pnl("LONG", Decimal("25000"), Decimal("25040"), 75) == Decimal("3000.00")
    assert futures_pnl("SHORT", Decimal("25000"), Decimal("25040"), 75) == Decimal("-3000.00")
    mark = mark_position(side="SHORT", entry_price=Decimal("25000"),
                         exit_price=Decimal("24980"), quantity_units=75,
                         margin_blocked=Decimal("225000"))
    assert (mark.pnl, mark.close_cash_flow) == (Decimal("1500.00"), Decimal("226500.00"))


def _ledger(close_flow: Decimal | None, realized: Decimal | None, cash: Decimal):
    account = PaperAccount(id=uuid4(), user_id=uuid4(), initial_cash=Decimal("1000000"),
                           cash_balance=cash)
    trade = PaperFuturesTrade(
        id=uuid4(), account_id=account.id, user_id=account.user_id,
        margin_blocked=Decimal("225000"), entry_cash_flow=Decimal("-225000"),
        status="OPEN" if close_flow is None else "CLOSED", realized_pnl=realized,
    )
    orders = [PaperFuturesOrder(id=uuid4(), account_id=account.id, user_id=account.user_id,
                                trade_id=trade.id, action="OPEN",
                                net_cash_flow=Decimal("-225000"))]
    if close_flow is not None:
        orders.append(PaperFuturesOrder(id=uuid4(), account_id=account.id,
                                        user_id=account.user_id, trade_id=trade.id,
                                        action="CLOSE", net_cash_flow=close_flow))
    return reconcile_paper_ledger(account, [], [], [], [trade], orders)


def test_reconciliation_counts_futures_margin_and_close_flows():
    assert _ledger(None, None, Decimal("775000")).status == "MATCHED"
    closed = _ledger(Decimal("228000"), Decimal("3000"), Decimal("1003000"))
    assert closed.status == "MATCHED"
    assert (closed.order_count, closed.closed_trade_count) == (2, 1)


def test_reconciliation_flags_futures_cash_or_pnl_drift():
    assert _ledger(None, None, Decimal("1000000")).status == "MISMATCH"
    drift = _ledger(Decimal("228000"), Decimal("2500"), Decimal("1003000"))
    assert drift.status == "MISMATCH"
    assert any("P&L differs" in issue for issue in drift.issues)
