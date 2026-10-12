from decimal import Decimal

from packages.options.models import PayoffRequest
from packages.options.payoff import calculate_expiry_payoff


def test_long_call_reports_breakeven_limited_loss_and_unbounded_profit():
    request = PayoffRequest(
        underlying="NIFTY",
        multiplier=75,
        price_min=0,
        price_max=200,
        grid_points=3,
        legs=[{"option_type": "CE", "strike": 100, "quantity": 1, "entry_premium": 5}],
    )

    result = calculate_expiry_payoff(request)

    assert result.breakeven_prices == [Decimal(105)]
    assert result.maximum_profit is None
    assert result.maximum_profit_unbounded is True
    assert result.maximum_loss == Decimal(375)
    assert result.maximum_loss_unbounded is False
    assert result.points[0].profit_loss == Decimal(-375)
    assert result.points[-1].profit_loss == Decimal(7125)


def test_iron_condor_returns_two_breakevens_and_defined_maximums():
    request = PayoffRequest(
        underlying="NIFTY",
        multiplier=100,
        price_min=80,
        price_max=130,
        grid_points=6,
        legs=[
            {"option_type": "PE", "strike": 90, "quantity": 1, "entry_premium": 2},
            {"option_type": "PE", "strike": 100, "quantity": -1, "entry_premium": 6},
            {"option_type": "CE", "strike": 110, "quantity": -1, "entry_premium": 6},
            {"option_type": "CE", "strike": 120, "quantity": 1, "entry_premium": 2},
        ],
    )

    result = calculate_expiry_payoff(request)

    assert result.breakeven_prices == [Decimal(92), Decimal(118)]
    assert result.maximum_profit == Decimal(800)
    assert result.maximum_loss == Decimal(200)
    assert result.maximum_profit_unbounded is False
    assert result.maximum_loss_unbounded is False
