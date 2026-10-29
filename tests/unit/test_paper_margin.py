from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from packages.paper_trading.margin import (
    MarginLeg,
    MarginParameters,
    estimate_position_margin,
    futures_margin,
    is_expiry_day,
    years_to_expiry,
)

PARAMS = MarginParameters(
    price_scan_pct=Decimal(9), volatility_scan_points=Decimal(4),
    exposure_pct=Decimal(2), short_option_minimum_pct=Decimal(3),
)
SPOT = Decimal(25000)
YEARS = 7 / 365


def _option(kind, strike, units):
    return MarginLeg(instrument=kind, quantity_units=units, underlying_price=SPOT,
                     strike=Decimal(strike), years_to_expiry=YEARS, volatility_pct=Decimal(14))


def test_futures_margin_is_price_scan_plus_exposure_either_direction():
    estimate = futures_margin(Decimal(25000), 75, PARAMS)
    assert estimate.scan_risk == Decimal("168750.00")
    assert estimate.exposure == Decimal("37500.00")
    assert estimate.total == Decimal("206250.00")
    short = estimate_position_margin(
        [MarginLeg(instrument="FUT", quantity_units=-75, underlying_price=SPOT)], PARAMS
    )
    assert short.total == estimate.total
    assert "+9.00%" in short.worst_scenario


def test_long_options_need_no_margin_beyond_the_premium_paid():
    estimate = estimate_position_margin([_option("CE", 25000, 75)], PARAMS)
    assert estimate.net_option_value > 0
    assert estimate.scan_risk <= estimate.net_option_value
    assert estimate.total == Decimal(0)


def test_naked_short_option_pays_scan_risk_or_minimum_plus_exposure():
    estimate = estimate_position_margin([_option("CE", 25500, -75)], PARAMS)
    assert estimate.short_option_minimum == Decimal("56250.00")
    assert estimate.exposure == Decimal("37500.00")
    assert estimate.scan_risk > estimate.short_option_minimum
    assert estimate.net_option_value < 0
    assert estimate.total == (
        estimate.scan_risk - estimate.net_option_value + estimate.exposure
    )


def test_hedged_spread_needs_far_less_than_the_naked_short():
    naked = estimate_position_margin([_option("CE", 25000, -75)], PARAMS)
    spread = estimate_position_margin(
        [_option("CE", 25000, -75), _option("CE", 25200, 75)], PARAMS
    )
    assert spread.short_option_minimum == 0
    # Scan risk plus the net credit held cannot exceed the 200-point width on 75 units.
    assert spread.scan_risk - spread.net_option_value <= Decimal(200 * 75)
    assert spread.total < naked.total / 2


def test_short_option_minimum_nets_only_within_each_option_type():
    covered_put = estimate_position_margin(
        [_option("PE", 24500, -75), _option("CE", 25500, 75)], PARAMS
    )
    assert covered_put.short_option_minimum == Decimal("56250.00")
    strangle = estimate_position_margin(
        [_option("PE", 24500, -75), _option("CE", 25500, -75)], PARAMS
    )
    assert strangle.short_option_minimum == Decimal("56250.00")


def test_bad_legs_are_rejected():
    with pytest.raises(ValueError):
        estimate_position_margin([], PARAMS)
    with pytest.raises(ValueError):
        futures_margin(Decimal(0), 75, PARAMS)
    with pytest.raises(ValueError):
        estimate_position_margin(
            [MarginLeg(instrument="PE", quantity_units=-75, underlying_price=SPOT)], PARAMS
        )


def test_years_to_expiry_counts_to_the_nse_close():
    before_close = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)  # 14:30 IST
    assert years_to_expiry(date(2026, 11, 24), before_close) == pytest.approx(3600 / 31_536_000)
    assert years_to_expiry(date(2026, 11, 23), before_close) == 0


def test_short_options_expiring_today_add_sebi_expiry_day_exposure():
    def short_put(expires_today):
        return MarginLeg(instrument="PE", quantity_units=-75, underlying_price=SPOT,
                         strike=Decimal(24800), years_to_expiry=0.0005,
                         volatility_pct=Decimal(14), expires_today=expires_today)

    normal = estimate_position_margin([short_put(False)], PARAMS)
    expiring = estimate_position_margin([short_put(True)], PARAMS)
    assert normal.expiry_day_exposure == 0
    assert expiring.expiry_day_exposure == Decimal("37500.00")  # 2% of 25000 x 75
    assert expiring.total == normal.total + Decimal("37500.00")
    assert expiring.snapshot()["expiry_day_exposure"] == "37500.00"


def test_long_options_expiring_today_carry_no_expiry_day_exposure():
    long_call = MarginLeg(instrument="CE", quantity_units=75, underlying_price=SPOT,
                          strike=Decimal(25000), years_to_expiry=0.0005,
                          volatility_pct=Decimal(14), expires_today=True)
    assert estimate_position_margin([long_call], PARAMS).expiry_day_exposure == 0


def test_expiry_day_uses_the_india_trading_date():
    late_utc = datetime(2026, 11, 23, 20, 0, tzinfo=timezone.utc)  # 01:30 IST on 24 Nov
    assert is_expiry_day(date(2026, 11, 24), late_utc)
    assert not is_expiry_day(date(2026, 11, 23), late_utc)
