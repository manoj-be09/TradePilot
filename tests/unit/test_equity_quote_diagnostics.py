from datetime import datetime, timedelta, timezone

import pytest

from packages.market_data.contracts import Quote
from packages.paper_trading.equity import quote_problem


@pytest.mark.parametrize(("field", "expected"), [
    ("timestamp", "Waiting for a valid quote timestamp within 60 seconds"),
    ("book_as_of", "Waiting for an exchange-timestamped book within 60 seconds"),
    ("last_trade_time", "Waiting for a verified exchange trade within 60 seconds"),
])
@pytest.mark.parametrize("offset", [-6, 61])
def test_failed_clock_is_identified_without_relaxing_freshness(field, expected, offset):
    now = datetime(2026, 10, 13, 5, tzinfo=timezone.utc)
    quote = Quote(
        instrument_token="test", timestamp=now, book_as_of=now, last_trade_time=now,
        timestamp_basis="exchange", book_timestamp_basis="exchange",
        last_price=100, bid=100, ask=100, bid_quantity=10, ask_quantity=10,
    )
    assert quote_problem(quote, "test", now) is None
    changed = quote.model_copy(update={field: now - timedelta(seconds=offset)})
    assert quote_problem(changed, "test", now) == expected


@pytest.mark.parametrize(("age", "allowed"), [(-6, False), (-5, True), (5, True), (6, False)])
def test_indicative_response_window_remains_five_seconds(age, allowed):
    now = datetime(2026, 10, 13, 5, tzinfo=timezone.utc)
    quote = Quote(
        instrument_token="test", timestamp=now - timedelta(seconds=age),
        timestamp_basis="provider_response", last_trade_time=now,
        last_price=100, bid=100, ask=100, bid_quantity=10, ask_quantity=10,
    )
    result = quote_problem(quote, "test", now, indicative=True)
    assert (result is None) == allowed
    assert quote_problem(quote, "test", now) == "Waiting for an exchange-timestamped bid/ask book"
