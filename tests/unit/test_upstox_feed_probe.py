"""The live-feed probe separates observed changes from exchange-time claims."""

from pathlib import Path
from runpy import run_path
from types import SimpleNamespace


def _helpers():
    script = run_path(str(Path("work/probe-upstox-v3-feed.py")))
    return script["observation_changes"], script["probe_conclusion"], script["clock_offset_seconds"]


def test_book_change_is_distinct_from_last_price_change():
    compare, _, _ = _helpers()
    first = SimpleNamespace(
        last_price=100, bid=99, ask=101, bid_quantity=50, ask_quantity=50
    )
    second = SimpleNamespace(
        last_price=100, bid=100, ask=101, bid_quantity=50, ask_quantity=50
    )
    _, _, first_book = compare(None, None, first)
    price_changed, book_changed, _ = compare(first.last_price, first_book, second)
    assert price_changed is False
    assert book_changed is True


def test_market_status_and_no_change_prevent_live_update_claim():
    _, conclude, _ = _helpers()
    assert "not established" in conclude("CLOSING_END", "CLOSING_END", 1, 1, 30)
    assert "not established" in conclude("NORMAL_OPEN", "NORMAL_CLOSE", 1, 1, 30)
    assert "not established" in conclude("NORMAL_OPEN", "NORMAL_OPEN", 0, 0, 30)
    assert "not established" in conclude("NORMAL_OPEN", "NORMAL_OPEN", 1, 1, 0.5)
    assert "exchange-origin book time remains unverified" in conclude(
        "NORMAL_OPEN", "NORMAL_OPEN", 0, 1, 30
    )


def test_clock_offset_uses_http_date_midpoint_and_rejects_invalid_input():
    from datetime import datetime, timezone

    _, _, offset = _helpers()
    before = datetime(2026, 10, 9, 7, 0, 0, tzinfo=timezone.utc)
    after = datetime(2026, 10, 9, 7, 0, 2, tzinfo=timezone.utc)
    assert offset("Fri, 09 Oct 2026 07:00:00 GMT", before, after) == 1
    assert offset("not-a-date", before, after) is None
