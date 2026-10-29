from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.backtesting.history_backtest import BacktestConfig, SkippedExpiry, run_expiry
from packages.backtesting.spread_model import calibrate_spreads, observed_half_spread
from packages.database.base import Base
from packages.database.recorder_models import MarketSnapshot1m, RecordedInstrument
from packages.history.spreads import recorded_option_quotes
from tests.unit.test_history_backtest import BULL_PUT, _history


def test_observed_half_spread_is_a_percentage_of_mid():
    mid, pct = observed_half_spread(Decimal("99"), Decimal("101"))
    assert mid == Decimal(100)
    assert pct == Decimal(1)


@pytest.mark.parametrize(("bid", "ask"), [
    (None, Decimal(10)), (Decimal(10), None), (Decimal(0), Decimal(1)),
    (Decimal(11), Decimal(10)),
])
def test_empty_or_crossed_quotes_are_rejected(bid, ask):
    assert observed_half_spread(bid, ask) is None


def test_bands_with_enough_quotes_use_the_upper_quantile():
    # 40 quotes around 120 with half-spreads 0.1%..4.0%; plus 5 cheap quotes.
    quotes = []
    for step in range(1, 41):
        half = Decimal(120) * Decimal(step) / Decimal(1000)
        quotes.append((Decimal(120) - half, Decimal(120) + half))
    quotes += [(Decimal(5), Decimal("5.10"))] * 5 + [(Decimal(0), Decimal(1))]
    model = calibrate_spreads(quotes, Decimal(1), min_observations=30)
    band = next(item for item in model.bands if item.lower == 100)
    assert band.source == "RECORDED"
    assert band.observations == 40
    assert band.quantile_pct == Decimal("3.0000")  # 30th of 40 sorted values
    assert model.half_spread_pct(Decimal(150)) == Decimal("3.0000")
    cheap = model.bands[0]
    assert cheap.source == "DEFAULT" and cheap.observations == 5
    assert model.half_spread_pct(Decimal(5)) == Decimal(1)
    assert model.rejected_quotes == 1
    assert model.snapshot()["model_id"] == "recorded-half-spread-by-premium-v1"


def test_calibration_inputs_are_validated():
    with pytest.raises(ValueError):
        calibrate_spreads([], Decimal(0))
    with pytest.raises(ValueError):
        calibrate_spreads([], Decimal(1), quantile=Decimal(1))


def test_backtest_fills_use_the_recorded_band_for_each_premium():
    # 30 quotes at mid 10 with a 5% half-spread fill the 10-25 band.
    model = calibrate_spreads([(Decimal("9.50"), Decimal("10.50"))] * 30, Decimal(1))
    config = BacktestConfig(strategy=BULL_PUT, spread_model=model)
    trade = run_expiry(_history(lambda _: Decimal(25010)), config)
    assert not isinstance(trade, SkippedExpiry)
    # Both legs trade at a close of 10 (time value only): sell at 9.50, buy at 10.50.
    assert [(leg.side, leg.entry_price) for leg in trade.legs] == [
        ("SELL", Decimal("9.50")), ("BUY", Decimal("10.50")),
    ]
    assert config.snapshot()["spread_model"]["bands"][1]["source"] == "RECORDED"


def test_recorded_quotes_are_options_inside_the_nse_session_only():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        MarketSnapshot1m.__table__, RecordedInstrument.__table__,
    ])
    day = date(2026, 10, 27)
    subscribed = datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc)
    with sessionmaker(engine)() as session:
        session.add_all([
            RecordedInstrument(trading_day=day, instrument_key="OPT1", kind="OPT",
                               first_subscribed_at=subscribed),
            RecordedInstrument(trading_day=day, instrument_key="FUT1", kind="FUT",
                               first_subscribed_at=subscribed),
        ])
        for key, minute_utc in (("OPT1", (4, 0)), ("OPT1", (3, 30)), ("FUT1", (4, 0)),
                                ("OPT1", (10, 0))):
            at = datetime(2026, 10, 27, *minute_utc, tzinfo=timezone.utc)
            session.add(MarketSnapshot1m(
                instrument_key=key, bucket_start=at, trading_day=day, received_at=at,
                bid=Decimal(10), ask=Decimal(11), message_count=1,
            ))
        session.commit()
        # 04:00 UTC = 09:30 IST is in session; 03:30 UTC (09:00) and 10:00 UTC (15:30) are not.
        quotes = list(recorded_option_quotes(session, start=day, end=day))
    assert quotes == [(Decimal(10), Decimal(11))]
