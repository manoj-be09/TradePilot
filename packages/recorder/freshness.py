"""How old was the market when a paper leg filled, according to the recorded live feed?

Fills use the option-chain API, which gives no time for the last trade or the bid/ask, so its
quotes can't be aged. The recorder's minute rows do carry each instrument's last trade time.
This is evidence only: it is saved beside a fill and never blocks one. A last trade time
dates the last trade, not the bid/ask.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from packages.database.recorder_models import MarketSnapshot1m

# Older than this, the recorder was probably down and the row says little about this fill.
MAX_RECORD_AGE = timedelta(minutes=10)


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def feed_freshness(session: Session, instrument_key: str, now: datetime) -> dict[str, Any]:
    try:
        with session.begin_nested():
            row = session.scalar(
                select(MarketSnapshot1m)
                .where(
                    MarketSnapshot1m.instrument_key == instrument_key,
                    MarketSnapshot1m.bucket_start >= now - MAX_RECORD_AGE,
                )
                .order_by(MarketSnapshot1m.bucket_start.desc())
                .limit(1)
            )
    except SQLAlchemyError:
        return {"feed_status": "UNAVAILABLE"}
    if row is None:
        return {"feed_status": "NO_RECENT_RECORD"}
    last_trade = _utc(row.last_trade_time) if row.last_trade_time else None
    return {
        "feed_status": "RECORDED" if last_trade else "NO_LAST_TRADE",
        "feed_observed_at": _utc(row.received_at).isoformat(),
        "feed_last_trade_time": last_trade.isoformat() if last_trade else None,
        "feed_last_trade_age_seconds": (
            round((now - last_trade).total_seconds(), 1) if last_trade else None
        ),
    }


def with_feed_freshness(
    session: Session, fills: Iterable[dict[str, Any]], now: datetime,
) -> list[dict[str, Any]]:
    return [{**fill, **feed_freshness(session, str(fill["instrument_key"]), now)} for fill in fills]


def summarize_ages(fills: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Ages across fill legs; legs with no usable age are counted, not guessed."""
    legs = list(fills)
    ages = sorted(
        float(leg["feed_last_trade_age_seconds"]) for leg in legs
        if leg.get("feed_last_trade_age_seconds") is not None
    )
    return {
        "legs": len(legs),
        "with_age": len(ages),
        "median_seconds": round(median(ages), 1) if ages else None,
        "p90_seconds": ages[int(0.9 * (len(ages) - 1))] if ages else None,
        "over_60_seconds": sum(age > 60 for age in ages),
    }
