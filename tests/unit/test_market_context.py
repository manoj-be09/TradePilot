"""Entry metrics from the clock and recorded index minutes; stale readings stay unknown."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.database.base import Base
from packages.database.recorder_models import MarketSnapshot1m
from packages.paper_trading.market_context import clock_metrics, recorded_index_metrics
from packages.paper_trading.rules import EntryCondition, evaluate_entry_conditions
from packages.recorder.universe import INDIA_VIX_KEY, NIFTY_INDEX_KEY

_DAY = date(2026, 11, 9)
_START = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)  # 09:30 IST


def _minute(key, moment, close, prev, high=None, low=None):
    return MarketSnapshot1m(
        instrument_key=key, bucket_start=moment, trading_day=_DAY, received_at=moment,
        ltp_open=Decimal(close), ltp_high=Decimal(high or close), ltp_low=Decimal(low or close),
        ltp_close=Decimal(close), prev_close=Decimal(prev), message_count=1)


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'rec.db').as_posix()}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all([
        _minute(NIFTY_INDEX_KEY, _START, "25100", "25000", high="25150", low="24900"),
        _minute(NIFTY_INDEX_KEY, _START + timedelta(minutes=1), "25050", "25000",
                high="25060", low="25040"),
        _minute(INDIA_VIX_KEY, _START + timedelta(minutes=1), "17.40", "18.43"),
    ])
    session.commit()
    return session


def test_fresh_recorded_minutes_give_vix_and_nifty_moves(tmp_path):
    with _session(tmp_path) as session:
        metrics = recorded_index_metrics(session, _START + timedelta(minutes=1, seconds=40))
    assert metrics == {
        "india_vix": Decimal("17.40"),
        "india_vix_change_pct": Decimal("-5.59"),
        "nifty_change_pct": Decimal("0.20"),
        "nifty_range_pct": Decimal("1.00"),  # 25150 - 24900 over the 25000 previous close
    }


def test_stale_or_missing_readings_are_unknown_so_the_entry_waits(tmp_path):
    with _session(tmp_path) as session:
        stale = recorded_index_metrics(session, _START + timedelta(minutes=5))
        before = recorded_index_metrics(session, _START - timedelta(minutes=1))
    assert set(stale.values()) == {None} and set(before.values()) == {None}
    [result] = evaluate_entry_conditions(
        [EntryCondition("india_vix", "GTE", Decimal("15"))], stale)
    assert result.passed is None


def test_clock_metrics_use_ist_open_and_calendar_days():
    assert clock_metrics(_START, date(2026, 11, 17)) == {
        "minutes_since_open": Decimal(15), "days_to_expiry": Decimal(8),
    }


def test_realized_volatility_needs_ten_returns_and_annualizes_them():
    from math import log, sqrt
    from statistics import stdev

    from packages.paper_trading.market_context import realized_vol_pct

    closes = [Decimal(25000 + 100 * ((-1) ** i) * (i % 3)) for i in range(12)]
    returns = [log(float(b) / float(a)) for a, b in zip(closes[1:], closes[2:], strict=False)]
    assert realized_vol_pct(closes) == Decimal(str(stdev(returns) * sqrt(252) * 100)).quantize(
        Decimal("0.01"))
    assert realized_vol_pct(closes[:10]) is None  # nine returns


def test_volatility_metrics_use_closes_before_today_once_a_day_and_wait_on_failure():
    import asyncio
    from types import SimpleNamespace

    from packages.market_data.contracts import MarketDataUnavailable
    from packages.paper_trading import market_context

    market_context._rv_cache.clear()
    now = datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc)  # 10:30 IST
    calls = []

    class Provider:
        fail = False

        async def get_historical(self, key, start, end, interval):
            calls.append((key, interval))
            if self.fail:
                raise MarketDataUnavailable("down")
            days = [now - timedelta(days=d) for d in range(12, -1, -1)]
            # Flat closes before today, so realized volatility is zero; today's jump is ignored.
            return [SimpleNamespace(timestamp=d, close=Decimal(30000 if d == now else 25000))
                    for d in days]

    provider = Provider()
    provider.fail = True
    failed = asyncio.run(market_context.volatility_metrics(provider, now, Decimal(14)))
    assert failed == {"realized_vol_10d_pct": None, "iv_minus_rv_points": None}
    provider.fail = False
    found = asyncio.run(market_context.volatility_metrics(provider, now, Decimal(14)))
    assert found == {"realized_vol_10d_pct": Decimal("0.00"),
                     "iv_minus_rv_points": Decimal("14.00")}
    asyncio.run(market_context.volatility_metrics(provider, now, None))
    assert calls == [(NIFTY_INDEX_KEY, "1d")] * 2  # cached after the first success
    assert asyncio.run(market_context.volatility_metrics(provider, now, None))[
        "iv_minus_rv_points"] is None
