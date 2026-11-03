"""Recorder minutes fold into the same 5-minute bars as history, and history always wins."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.backtesting.history_backtest import _IST
from packages.backtesting.published_strategies import Bar, Book
from packages.database.base import Base
from packages.database.recorder_models import MarketSnapshot1m, RecordedInstrument
from packages.learning.forward_bars import merge_books, recorded_books

_DAY = date(2026, 11, 4)
_EXPIRY = date(2026, 11, 10)


def _minute(key: str, moment: datetime, price: float) -> MarketSnapshot1m:
    value = Decimal(str(price))
    return MarketSnapshot1m(
        instrument_key=key, bucket_start=moment, trading_day=_DAY, received_at=moment,
        ltp_open=value, ltp_high=value + 1, ltp_low=value - 1, ltp_close=value, message_count=1)


def test_minutes_fold_into_five_minute_ist_bars_inside_regular_hours(tmp_path):
    url = f"sqlite:///{(tmp_path / 'rec.db').as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    start = datetime(2026, 11, 4, 3, 50, tzinfo=timezone.utc)  # 09:20 IST
    with Session(engine) as session:
        session.add(RecordedInstrument(
            trading_day=_DAY, instrument_key="NSE_FO|1", kind="OPT", expiry=_EXPIRY,
            strike=Decimal("24000"), option_type="CE", first_subscribed_at=start))
        session.add(RecordedInstrument(
            trading_day=_DAY, instrument_key="NSE_INDEX|Nifty 50", kind="INDEX",
            first_subscribed_at=start))
        session.add_all([_minute("NSE_FO|1", start + timedelta(minutes=offset), 100 + offset)
                         for offset in range(7)])
        # Before the open (09:10 IST) is ignored, as is a non-option instrument.
        session.add(_minute("NSE_FO|1", start - timedelta(minutes=10), 1))
        session.add(_minute("NSE_INDEX|Nifty 50", start, 24000))
        session.commit()
    [book] = recorded_books(url, _DAY)
    series = book.bars[(Decimal("24000"), "CE")]
    first = datetime(2026, 11, 4, 9, 20, tzinfo=_IST)
    assert list(series) == [first, first + timedelta(minutes=5)]
    assert series[first] == Bar(100.0, 105.0, 99.0, 104.0)
    assert series[first + timedelta(minutes=5)] == Bar(105.0, 107.0, 104.0, 106.0)
    assert recorded_books(url, _DAY + timedelta(days=1)) == []


def test_history_wins_and_recorder_fills_only_sessions_history_lacks():
    old = datetime(2026, 11, 3, 9, 20, tzinfo=_IST)
    new = datetime(2026, 11, 4, 9, 20, tzinfo=_IST)
    key = (Decimal("24000.00"), "CE")
    history = [Book(_EXPIRY, {key: {old: Bar(1, 1, 1, 1)}})]
    recorded = [Book(_EXPIRY, {(Decimal("24000"), "CE"): {old: Bar(9, 9, 9, 9),
                                                           new: Bar(2, 2, 2, 2)}})]
    [book] = merge_books(history, recorded)
    assert book.bars[key] == {old: Bar(1, 1, 1, 1), new: Bar(2, 2, 2, 2)}
    assert sorted(book.moments_by_day) == [old.date(), new.date()]


def test_vix_closes_come_from_the_recorder(tmp_path):
    from packages.learning.features import Context

    url = f"sqlite:///{(tmp_path / 'rec.db').as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    start = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
    with Session(engine) as session:
        session.add_all([_minute("NSE_INDEX|India VIX", start, 13.0),
                         _minute("NSE_INDEX|India VIX", start + timedelta(minutes=1), 13.4)])
        session.commit()
    events = tmp_path / "events.json"
    events.write_text('[{"date": "2026-11-05", "name": "RBI policy"}]')
    context = Context.load(url, events, tmp_path / "missing.json")
    assert context.vix == {_DAY: 13.4}
    assert context.events == frozenset({date(2026, 11, 5)})
