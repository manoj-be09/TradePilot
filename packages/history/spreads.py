"""Read recorded option bid/ask during NSE sessions for spread calibration."""

from collections.abc import Iterator
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from packages.database.recorder_models import MarketSnapshot1m, RecordedInstrument

_IST = ZoneInfo("Asia/Kolkata")
_OPEN, _CLOSE = time(9, 15), time(15, 30)


def recorded_option_quotes(
    session: Session, *, start: date, end: date
) -> Iterator[tuple[Decimal | None, Decimal | None]]:
    """(bid, ask) of every recorded option minute between 09:15 and 15:30 IST."""
    rows = session.execute(
        select(MarketSnapshot1m.bucket_start, MarketSnapshot1m.bid, MarketSnapshot1m.ask)
        .join(RecordedInstrument, and_(
            RecordedInstrument.trading_day == MarketSnapshot1m.trading_day,
            RecordedInstrument.instrument_key == MarketSnapshot1m.instrument_key,
        ))
        .where(
            RecordedInstrument.kind == "OPT",
            MarketSnapshot1m.trading_day >= start,
            MarketSnapshot1m.trading_day <= end,
        )
        .execution_options(yield_per=5000)
    )
    for bucket_start, bid, ask in rows:
        aware: datetime = bucket_start if bucket_start.tzinfo else bucket_start.replace(
            tzinfo=timezone.utc
        )
        moment = aware.astimezone(_IST)
        if _OPEN <= moment.time() < _CLOSE:
            yield bid, ask
