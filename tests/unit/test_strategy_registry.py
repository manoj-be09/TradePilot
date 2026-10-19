import pytest

from packages.paper_trading.strategies import StrategyDefinition, StrategyLeg, StrategyRegistry


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
