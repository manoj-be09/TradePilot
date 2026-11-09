import pytest

from packages.options.models import PayoffRequest
from packages.options.payoff import calculate_expiry_payoff
from packages.paper_trading.strategies import (
    StrategyDefinition,
    StrategyLeg,
    StrategyRegistry,
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


def test_only_a_long_call_calendar_may_span_two_expiries():
    near, far = StrategyLeg("CE", "SELL", 0), StrategyLeg("CE", "BUY", 0, expiry_offset=1)
    assert _calendar(near, far).calendars()[0].key == "cal"
    assert _calendar(near, far).list() == ()  # backtests and research never see it
    with pytest.raises(ValueError, match="inconsistent expiry scope"):
        _calendar(near, far, scope="SAME_EXPIRY")
    with pytest.raises(ValueError, match="inconsistent expiry scope"):
        _calendar(near, StrategyLeg("CE", "BUY", 0))
    with pytest.raises(ValueError, match="long call calendar"):
        _calendar(StrategyLeg("PE", "SELL", 0), StrategyLeg("PE", "BUY", 0, expiry_offset=1))
    with pytest.raises(ValueError, match="long call calendar"):  # short the far leg
        _calendar(StrategyLeg("CE", "BUY", 0), StrategyLeg("CE", "SELL", 0, expiry_offset=1))
    with pytest.raises(ValueError, match="long call calendar"):  # different strikes
        _calendar(near, StrategyLeg("CE", "BUY", 1, expiry_offset=1))
    with pytest.raises(ValueError):
        StrategyLeg("CE", "BUY", 0, expiry_offset=2)
    assert get_strategy("call_calendar").expiry_scope == "MULTI_EXPIRY"
