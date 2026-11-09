from decimal import Decimal

import pytest

from packages.options.models import PayoffRequest
from packages.options.payoff import calculate_expiry_payoff
from packages.paper_trading.strategies import (
    StrategyDefinition,
    StrategyLeg,
    StrategyRegistry,
    calendar_max_loss,
    get_strategy,
)


@pytest.mark.parametrize(
    ("key", "expected_payoffs", "version"),
    [("strip", [25, -15, 5], "2.0.0"), ("strap", [5, -15, 25], "1.0.0")],
)
def test_strip_and_strap_have_opposite_directional_payoffs(key, expected_payoffs, version):
    strategy = get_strategy(key)
    assert strategy is not None and strategy.version == version
    result = calculate_expiry_payoff(PayoffRequest(
        underlying="NIFTY", multiplier=1, price_min=80, price_max=120, grid_points=3,
        legs=[{
            "option_type": leg.option_type, "strike": 100,
            "quantity": leg.quantity_ratio, "entry_premium": 5,
        } for leg in strategy.legs],
    ))
    assert [point.profit_loss for point in result.points] == expected_payoffs
    assert result.maximum_loss == 15
    assert result.maximum_profit_unbounded is True
    assert result.maximum_loss_unbounded is False


def test_registered_templates_have_bounded_call_tail():
    registry = StrategyRegistry(
        (
            StrategyDefinition(
                "bounded",
                "Bounded",
                "A long call",
                (StrategyLeg("CE", "BUY", 0),),
            ),
        )
    )
    assert registry.get("bounded") is not None


def test_defined_risk_registry_rejects_naked_short_call():
    with pytest.raises(ValueError, match="unbounded call-side loss"):
        StrategyRegistry(
            (
                StrategyDefinition(
                    "unsafe",
                    "Unsafe",
                    "A naked short call",
                    (StrategyLeg("CE", "SELL", 0),),
                ),
            )
        )


def _calendar(*legs, scope="MULTI_EXPIRY"):
    return StrategyRegistry((StrategyDefinition("cal", "Cal", "x", legs, expiry_scope=scope),))


def test_only_bounded_calendars_and_diagonals_may_span_two_expiries():
    near, far = StrategyLeg("CE", "SELL", 0), StrategyLeg("CE", "BUY", 0, expiry_offset=1)
    assert _calendar(near, far).calendars()[0].key == "cal"
    assert _calendar(near, far).list() == ()  # backtests and research never see it
    assert _calendar(StrategyLeg("PE", "SELL", 0), StrategyLeg("PE", "BUY", 0, expiry_offset=1))
    assert _calendar(StrategyLeg("CE", "SELL", 2), StrategyLeg("CE", "BUY", 0, expiry_offset=1))
    assert _calendar(StrategyLeg("PE", "SELL", -2), StrategyLeg("PE", "BUY", 0, expiry_offset=1))
    with pytest.raises(ValueError, match="inconsistent expiry scope"):
        _calendar(near, far, scope="SAME_EXPIRY")
    with pytest.raises(ValueError, match="inconsistent expiry scope"):
        _calendar(near, StrategyLeg("CE", "BUY", 0))
    unsafe = (
        (StrategyLeg("CE", "BUY", 0), StrategyLeg("CE", "SELL", 0, expiry_offset=1)),  # short far
        (near, StrategyLeg("CE", "BUY", 1, expiry_offset=1)),  # bought call above the sold one
        (StrategyLeg("PE", "SELL", 0), StrategyLeg("PE", "BUY", -1, expiry_offset=1)),  # put below
        (StrategyLeg("CE", "SELL", 0, 2), far),  # two sold, one bought
        (StrategyLeg("PE", "SELL", 0), far),  # a call cannot cover a put
        (near, far, StrategyLeg("PE", "BUY", 0)),  # a near leg that is bought
    )
    for legs in unsafe:
        with pytest.raises(ValueError, match="protected side"):
            _calendar(*legs)
    with pytest.raises(ValueError):
        StrategyLeg("CE", "BUY", 0, expiry_offset=2)
    for key in ("call_calendar", "calendar_put_spread", "diagonal_call_spread",
                "diagonal_put_spread", "double_diagonal"):
        assert get_strategy(key).expiry_scope == "MULTI_EXPIRY"


def test_calendar_max_loss_is_the_debit_plus_a_put_below_intrinsic_allowance():
    k = Decimal(25000)
    assert calendar_max_loss(
        [("CE", k, -1, False), ("CE", k, 1, True)], Decimal(-3250), 65, 7) == 3250
    # A bought call below the sold one is worth at least the sold one at the near expiry.
    assert calendar_max_loss(
        [("CE", k + 100, -1, False), ("CE", k, 1, True)], Decimal(-5850), 65, 7) == 5850
    put = calendar_max_loss([("PE", k, -1, False), ("PE", k, 1, True)], Decimal(-3250), 65, 7)
    allowance = k * (1 - 1 / (1 + Decimal("0.10") * 7 / 365)) * 65
    assert put == 3250 + allowance and 3250 + 3100 < put < 3250 + 3150
    with pytest.raises(ValueError, match="short calls"):
        calendar_max_loss([("CE", k, -2, False), ("CE", k, 1, True)], Decimal(-1), 65, 7)
