"""Fills save the recorded feed's last-trade age as evidence and are never blocked by it."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.database import futures_models  # noqa: F401
from packages.database.base import Base
from packages.database.recorder_models import MarketSnapshot1m
from packages.recorder.freshness import feed_freshness, summarize_ages, with_feed_freshness

NOW = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)
KEY = "NSE_FO|1"


@pytest.fixture
def session():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as active:
        yield active


def _row(session, minutes_ago: int, last_trade_seconds_ago: float | None, key: str = KEY) -> None:
    bucket = NOW - timedelta(minutes=minutes_ago)
    session.add(MarketSnapshot1m(
        instrument_key=key, bucket_start=bucket, trading_day=date(2026, 11, 3),
        received_at=bucket + timedelta(seconds=59),
        last_trade_time=(
            NOW - timedelta(seconds=last_trade_seconds_ago)
            if last_trade_seconds_ago is not None else None
        ),
        message_count=1,
    ))
    session.commit()


def test_uses_the_latest_recent_row(session):
    _row(session, 5, 400)
    _row(session, 1, 12.5)
    result = feed_freshness(session, KEY, NOW)
    assert result["feed_status"] == "RECORDED"
    assert result["feed_last_trade_age_seconds"] == 12.5
    assert result["feed_observed_at"].startswith("2026-11-03T05:59:59")


def test_a_row_without_a_last_trade_is_reported_not_guessed(session):
    _row(session, 1, None)
    result = feed_freshness(session, KEY, NOW)
    assert result["feed_status"] == "NO_LAST_TRADE"
    assert result["feed_last_trade_age_seconds"] is None


@pytest.mark.parametrize("minutes_ago", [11, 60])
def test_an_old_row_means_the_recorder_was_probably_down(session, minutes_ago):
    _row(session, minutes_ago, 5)
    assert feed_freshness(session, KEY, NOW) == {"feed_status": "NO_RECENT_RECORD"}


def test_another_instrument_is_not_used(session):
    _row(session, 1, 5, key="NSE_FO|2")
    assert feed_freshness(session, KEY, NOW) == {"feed_status": "NO_RECENT_RECORD"}


def test_a_missing_table_never_blocks_a_fill_or_breaks_the_transaction():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    with sessionmaker(engine)() as session:
        assert feed_freshness(session, KEY, NOW) == {"feed_status": "UNAVAILABLE"}
        session.commit()


def test_fills_keep_their_fields_and_gain_the_evidence(session):
    _row(session, 1, 30)
    fills = with_feed_freshness(session, [{"instrument_key": KEY, "fill_price": "10"}], NOW)
    assert fills[0]["fill_price"] == "10"
    assert fills[0]["feed_last_trade_age_seconds"] == 30


def test_summary_counts_legs_without_an_age():
    summary = summarize_ages([
        {"feed_last_trade_age_seconds": 5.0}, {"feed_last_trade_age_seconds": 100.0},
        {"feed_last_trade_age_seconds": 7.0}, {"feed_status": "NO_RECENT_RECORD"},
    ])
    assert summary == {"legs": 4, "with_age": 3, "median_seconds": 7.0,
                       "p90_seconds": 7.0, "over_60_seconds": 1}
    assert summarize_ages([])["median_seconds"] is None
