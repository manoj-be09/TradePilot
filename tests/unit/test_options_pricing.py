from datetime import datetime, timedelta, timezone
from decimal import Decimal

from packages.options.models import OptionGreeksInput
from packages.options.pricing import calculate_greeks


def _input(market_price: str | None = None) -> OptionGreeksInput:
    valuation = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return OptionGreeksInput(
        option_type="CE",
        underlying_price=100,
        strike=100,
        valuation_time=valuation,
        expiration_time=valuation + timedelta(days=365),
        volatility="0.20",
        risk_free_rate="0",
        dividend_yield="0",
        market_price=market_price,
    )


def test_black_scholes_price_and_greeks_have_expected_atm_values():
    result = calculate_greeks(_input())

    assert abs(result.model_price - Decimal("7.965")) < Decimal("0.002")
    assert abs(result.delta - Decimal("0.54")) < Decimal("0.002")
    assert result.gamma is not None and result.gamma > 0
    assert result.theta_per_calendar_day is not None and result.theta_per_calendar_day < 0
    assert result.vega_per_volatility_point is not None and result.vega_per_volatility_point > 0
    assert result.implied_volatility is None


def test_implied_volatility_solver_recovers_model_input():
    result = calculate_greeks(_input("7.965567455405804"))

    assert result.implied_volatility is not None
    assert abs(result.implied_volatility - Decimal("0.20")) < Decimal("0.000001")
    assert result.implied_volatility_status == "solved"
