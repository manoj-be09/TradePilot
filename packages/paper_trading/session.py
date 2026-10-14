"""Exchange-local date checks for paper option expiry.

This does not determine whether a session is open or settle expired positions.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from packages.market_data.contracts import ExchangeSession

_NSE_TIMEZONE = ZoneInfo("Asia/Kolkata")


def exchange_today(now: datetime | None = None) -> date:
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("Exchange date requires a timezone-aware timestamp")
    return instant.astimezone(_NSE_TIMEZONE).date()


def expiry_has_passed(expiry: date, now: datetime | None = None) -> bool:
    return expiry < exchange_today(now)


class PaperSessionError(ValueError):
    pass


def require_nfo_session_open(
    session: ExchangeSession | None, now: datetime | None = None
) -> None:
    require_derivatives_session_open(session, "NFO", now)


def require_derivatives_session_open(
    session: ExchangeSession | None, expected_exchange: str, now: datetime | None = None
) -> None:
    """Validate the selected venue; an open NSE session cannot authorize BSE fills."""
    if expected_exchange not in {"NFO", "BFO"}:
        raise PaperSessionError("Unsupported derivatives venue; no paper fill recorded")
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("Session check requires a timezone-aware timestamp")
    if session is None:
        raise PaperSessionError(
            f"{expected_exchange} has no scheduled trading session today; no paper fill recorded"
        )
    if (
        session.exchange != expected_exchange
        or session.start.tzinfo is None
        or session.end.tzinfo is None
        or session.start >= session.end
    ):
        raise PaperSessionError(
            f"{expected_exchange} scheduled session data is invalid; no paper fill recorded"
        )
    if not session.start <= instant < session.end:
        raise PaperSessionError(
            f"{expected_exchange} scheduled session is closed; no paper fill recorded"
        )


def expiry_square_off_due(
    expiry: date,
    session: ExchangeSession | None,
    *,
    now: datetime | None = None,
    minutes_before_close: int = 20,
    expected_exchange: str = "NFO",
) -> bool:
    """Return whether an expiring option is in the provider's final session window."""
    instant = now or datetime.now(timezone.utc)
    require_derivatives_session_open(session, expected_exchange, instant)
    assert session is not None
    if minutes_before_close <= 0:
        raise PaperSessionError("NFO scheduled session data is invalid; no paper fill recorded")
    if expiry != exchange_today(instant):
        return False
    cutoff = max(session.start, session.end - timedelta(minutes=minutes_before_close))
    return instant >= cutoff


def expiry_entry_window_closed(
    expiry: date,
    session: ExchangeSession | None,
    *,
    now: datetime | None = None,
    minutes_before_close: int = 20,
    expected_exchange: str = "NFO",
) -> bool:
    """An unfilled expiry-day deployment cannot enter after the exit cutoff."""
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("Expiry entry check requires a timezone-aware timestamp")
    if expiry != exchange_today(instant):
        return False
    if session is None:
        return True
    if (
        expected_exchange not in {"NFO", "BFO"}
        or session.exchange != expected_exchange
        or session.start.tzinfo is None
        or session.end.tzinfo is None
        or session.start >= session.end
        or minutes_before_close <= 0
    ):
        raise PaperSessionError("NFO scheduled session data is invalid; entry remains waiting")
    cutoff = max(session.start, session.end - timedelta(minutes=minutes_before_close))
    return instant >= cutoff
