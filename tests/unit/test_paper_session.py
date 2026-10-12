from datetime import date, datetime, timedelta, timezone

import pytest

from packages.market_data.contracts import ExchangeSession
from packages.paper_trading.session import (
    PaperSessionError,
    exchange_today,
    expiry_entry_window_closed,
    expiry_has_passed,
    expiry_square_off_due,
    require_nfo_session_open,
)


def test_exchange_date_uses_india_clock_at_utc_day_boundary():
    before_midnight = datetime(2026, 9, 26, 18, 29, tzinfo=timezone.utc)
    after_midnight = datetime(2026, 9, 26, 18, 30, tzinfo=timezone.utc)

    assert exchange_today(before_midnight) == date(2026, 9, 26)
    assert exchange_today(after_midnight) == date(2026, 9, 27)
    assert not expiry_has_passed(date(2026, 9, 27), after_midnight)
    assert expiry_has_passed(date(2026, 9, 26), after_midnight)


def test_exchange_date_rejects_naive_clock():
    with pytest.raises(ValueError, match="timezone-aware"):
        exchange_today(datetime(2026, 9, 26, 18, 30))


def test_session_guard_rejects_missing_and_outside_schedule():
    start = datetime(2026, 9, 28, 3, 45, tzinfo=timezone.utc)
    session = ExchangeSession(exchange="NFO", start=start, end=start + timedelta(hours=6))

    require_nfo_session_open(session, start + timedelta(minutes=1))
    with pytest.raises(PaperSessionError, match="no scheduled"):
        require_nfo_session_open(None, start + timedelta(minutes=1))
    with pytest.raises(PaperSessionError, match="closed"):
        require_nfo_session_open(session, start - timedelta(seconds=1))
    with pytest.raises(PaperSessionError, match="closed"):
        require_nfo_session_open(session, session.end)


def test_expiry_square_off_uses_provider_session_end_and_exchange_date():
    start = datetime(2026, 9, 28, 3, 45, tzinfo=timezone.utc)
    session = ExchangeSession(exchange="NFO", start=start, end=start + timedelta(hours=6))
    expiry = date(2026, 9, 28)
    assert not expiry_square_off_due(expiry, session, now=session.end - timedelta(minutes=21))
    assert expiry_square_off_due(expiry, session, now=session.end - timedelta(minutes=20))
    assert not expiry_square_off_due(
        date(2026, 9, 29), session, now=session.end - timedelta(minutes=5)
    )
    with pytest.raises(PaperSessionError, match="closed"):
        expiry_square_off_due(expiry, session, now=session.end)
    with pytest.raises(PaperSessionError, match="invalid"):
        expiry_square_off_due(expiry, ExchangeSession("NFO", session.end, start), now=start)


def test_unfilled_expiry_entry_cutoff_includes_closed_session_and_no_session():
    start = datetime(2026, 9, 28, 3, 45, tzinfo=timezone.utc)
    session = ExchangeSession("NFO", start, start + timedelta(hours=6))
    expiry = date(2026, 9, 28)
    assert not expiry_entry_window_closed(
        expiry, session, now=session.end - timedelta(minutes=21)
    )
    assert expiry_entry_window_closed(
        expiry, session, now=session.end - timedelta(minutes=20)
    )
    assert expiry_entry_window_closed(expiry, session, now=session.end)
    assert expiry_entry_window_closed(expiry, None, now=start)
    assert not expiry_entry_window_closed(date(2026, 9, 29), None, now=start)
    with pytest.raises(PaperSessionError, match="invalid"):
        expiry_entry_window_closed(
            expiry, ExchangeSession("NFO", session.end, start), now=start
        )
