from dataclasses import dataclass
from typing import Literal

OptionType = Literal["CE", "PE"]
TradeSide = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class StrategyLeg:
    option_type: OptionType
    side: TradeSide
    strike_offset: int


@dataclass(frozen=True)
class StrategyDefinition:
    key: str
    name: str
    description: str
    legs: tuple[StrategyLeg, ...]
    version: str = "1.0.0"
    risk_profile: str = "DEFINED_RISK"
    lifecycle_status: str = "PAPER_TEMPLATE"


class StrategyRegistry:
    """Immutable definitions for strategies available to paper trading."""

    def __init__(self, strategies: tuple[StrategyDefinition, ...]) -> None:
        by_key = {strategy.key: strategy for strategy in strategies}
        if len(by_key) != len(strategies):
            raise ValueError("Strategy keys must be unique")
        if any(not strategy.legs for strategy in strategies):
            raise ValueError("Every strategy must define at least one option leg")
        self._strategies = strategies
        self._by_key = by_key

    def list(self) -> tuple[StrategyDefinition, ...]:
        return self._strategies

    def get(self, key: str) -> StrategyDefinition | None:
        return self._by_key.get(key)


STRATEGY_REGISTRY = StrategyRegistry(
    (
        StrategyDefinition(
            "bull_call_spread",
            "Bull call spread",
            "Buy a call at the reference strike and sell the next higher-strike call.",
            (StrategyLeg("CE", "BUY", 0), StrategyLeg("CE", "SELL", 1)),
        ),
        StrategyDefinition(
            "bear_put_spread",
            "Bear put spread",
            "Buy a put at the reference strike and sell the next lower-strike put.",
            (StrategyLeg("PE", "BUY", 0), StrategyLeg("PE", "SELL", -1)),
        ),
        StrategyDefinition(
            "bull_put_spread",
            "Bull put spread",
            "Sell a put at the reference strike and buy the next lower-strike put.",
            (StrategyLeg("PE", "SELL", 0), StrategyLeg("PE", "BUY", -1)),
        ),
        StrategyDefinition(
            "bear_call_spread",
            "Bear call spread",
            "Sell a call at the reference strike and buy the next higher-strike call.",
            (StrategyLeg("CE", "SELL", 0), StrategyLeg("CE", "BUY", 1)),
        ),
        StrategyDefinition(
            "iron_condor",
            "Iron condor",
            "Sell the adjacent put and call wings, protected by one further strike on each side.",
            (
                StrategyLeg("PE", "BUY", -2),
                StrategyLeg("PE", "SELL", -1),
                StrategyLeg("CE", "SELL", 1),
                StrategyLeg("CE", "BUY", 2),
            ),
        ),
        StrategyDefinition(
            "long_straddle",
            "Long straddle",
            "Buy a call and put at the reference strike.",
            (StrategyLeg("CE", "BUY", 0), StrategyLeg("PE", "BUY", 0)),
        ),
        StrategyDefinition(
            "long_strangle",
            "Long strangle",
            "Buy a put one strike below and a call one strike above the reference strike.",
            (StrategyLeg("PE", "BUY", -1), StrategyLeg("CE", "BUY", 1)),
        ),
        StrategyDefinition(
            "long_call",
            "Long call",
            "Buy one call at the reference strike. Maximum loss is the premium paid.",
            (StrategyLeg("CE", "BUY", 0),),
        ),
        StrategyDefinition(
            "long_put",
            "Long put",
            "Buy one put at the reference strike. Maximum loss is the premium paid.",
            (StrategyLeg("PE", "BUY", 0),),
        ),
    )
)
STRATEGIES = STRATEGY_REGISTRY.list()


def get_strategy(key: str) -> StrategyDefinition | None:
    return STRATEGY_REGISTRY.get(key)
