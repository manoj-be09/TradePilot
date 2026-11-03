"""5-minute forward bars from the private recorder, so shadow scoring needs no API calls.

The recorder stores one row per option per minute (last-price OHLC). Five of them fold into the
same 5-minute bar the history importer stores, and those books join the imported history: an
imported bar always wins, and recorder bars only fill sessions history does not have yet, such as
the expiries still live. Private data; never redistributed.
"""

from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import cast

from sqlalchemy import select

from packages.backtesting.history_backtest import _ist
from packages.backtesting.published_strategies import Bar, Book, Kind
from packages.database.recorder_models import MarketSnapshot1m, RecordedInstrument
from packages.database.session import build_session_factory

_OPEN, _CLOSE = time(9, 15), time(15, 30)
_FIVE = 5


def _bucket(moment: datetime) -> datetime:
    return moment.replace(minute=moment.minute - moment.minute % _FIVE, second=0, microsecond=0)


def recorded_books(database_url: str, first_day: date) -> list[Book]:
    """Fold recorded minutes from `first_day` on into 5-minute IST bars, one book per expiry."""
    folded: dict[date, dict[tuple[Decimal, Kind], dict[datetime, list[float]]]] = {}
    with build_session_factory(database_url)() as session:
        rows = session.execute(
            select(RecordedInstrument.expiry, RecordedInstrument.strike,
                   RecordedInstrument.option_type, MarketSnapshot1m.bucket_start,
                   MarketSnapshot1m.ltp_open, MarketSnapshot1m.ltp_high, MarketSnapshot1m.ltp_low,
                   MarketSnapshot1m.ltp_close)
            .join(RecordedInstrument,
                  (RecordedInstrument.instrument_key == MarketSnapshot1m.instrument_key)
                  & (RecordedInstrument.trading_day == MarketSnapshot1m.trading_day))
            .where(RecordedInstrument.kind == "OPT", MarketSnapshot1m.trading_day >= first_day,
                   MarketSnapshot1m.ltp_close.is_not(None))
            .order_by(MarketSnapshot1m.bucket_start)
        )
        for expiry, strike, kind, start, opened, high, low, closed in rows:
            moment = _ist(start)
            if expiry is None or strike is None or closed is None or kind not in ("CE", "PE") or (
                    not _OPEN <= moment.time() < _CLOSE):
                continue
            close = float(closed)
            minute = [float(opened or closed), float(high or closed), float(low or closed), close]
            series = folded.setdefault(expiry, {}).setdefault(
                (Decimal(strike), cast(Kind, kind)), {})
            bar = series.get(_bucket(moment))
            if bar is None:
                series[_bucket(moment)] = minute
            else:
                bar[1], bar[2], bar[3] = max(bar[1], minute[1]), min(bar[2], minute[2]), close
    return [Book(expiry, {key: {moment: Bar(*values) for moment, values in series.items()}
                          for key, series in by_key.items()})
            for expiry, by_key in sorted(folded.items())]


def merge_books(history: Sequence[Book], recorded: Sequence[Book]) -> list[Book]:
    """History's bars win; recorder bars fill only the (expiry, session) pairs history lacks."""
    merged = {book.expiry: {key: dict(series) for key, series in book.bars.items()}
              for book in history}
    covered = {(book.expiry, day) for book in history for day in book.moments_by_day}
    for book in recorded:
        target = merged.setdefault(book.expiry, {})
        for key, series in book.bars.items():
            for moment, bar in series.items():
                if (book.expiry, moment.date()) not in covered:
                    target.setdefault(key, {})[moment] = bar
    return [Book(expiry, by_key) for expiry, by_key in sorted(merged.items())]

