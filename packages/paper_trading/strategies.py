from dataclasses import dataclass
from typing import Literal

OptionType = Literal["CE", "PE"]
TradeSide = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class StrategyLeg:
    option_type: OptionType
    side: TradeSide
    strike_offset: int
    quantity_ratio: int = 1

    def __post_init__(self) -> None:
        if self.quantity_ratio < 1:
            raise ValueError("Strategy leg quantity ratio must be at least one")


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
            "iron_butterfly",
            "Iron butterfly",
            "Sell the reference-strike put and call, protected by one wing on each side.",
            (
                StrategyLeg("PE", "BUY", -1),
                StrategyLeg("PE", "SELL", 0),
                StrategyLeg("CE", "SELL", 0),
                StrategyLeg("CE", "BUY", 1),
            ),
        ),
        StrategyDefinition(
            "long_call_condor",
            "Long call condor",
            "Buy outer calls and sell the two inner calls to create a wider defined-risk range.",
            (
                StrategyLeg("CE", "BUY", -2),
                StrategyLeg("CE", "SELL", -1),
                StrategyLeg("CE", "SELL", 1),
                StrategyLeg("CE", "BUY", 2),
            ),
        ),
        StrategyDefinition(
            "long_put_condor",
            "Long put condor",
            "Buy outer puts and sell the two inner puts to create a wider defined-risk range.",
            (
                StrategyLeg("PE", "BUY", -2),
                StrategyLeg("PE", "SELL", -1),
                StrategyLeg("PE", "SELL", 1),
                StrategyLeg("PE", "BUY", 2),
            ),
        ),
        StrategyDefinition(
            "long_call_butterfly",
            "Long call butterfly",
            "Buy one lower-strike call, sell two middle calls, and buy one higher-strike call.",
            (
                StrategyLeg("CE", "BUY", -1),
                StrategyLeg("CE", "SELL", 0, 2),
                StrategyLeg("CE", "BUY", 1),
            ),
        ),
        StrategyDefinition(
            "long_put_butterfly",
            "Long put butterfly",
            "Buy one lower-strike put, sell two middle puts, and buy one higher-strike put.",
            (
                StrategyLeg("PE", "BUY", -1),
                StrategyLeg("PE", "SELL", 0, 2),
                StrategyLeg("PE", "BUY", 1),
            ),
        ),
        StrategyDefinition(
            "short_call_butterfly",
            "Short call butterfly",
            "Sell the outer calls and buy two middle calls for a bounded move-seeking structure.",
            (
                StrategyLeg("CE", "SELL", -1),
                StrategyLeg("CE", "BUY", 0, 2),
                StrategyLeg("CE", "SELL", 1),
            ),
        ),
        StrategyDefinition(
            "short_put_butterfly",
            "Short put butterfly",
            "Sell the outer puts and buy two middle puts for a bounded move-seeking structure.",
            (
                StrategyLeg("PE", "SELL", -1),
                StrategyLeg("PE", "BUY", 0, 2),
                StrategyLeg("PE", "SELL", 1),
            ),
        ),
        StrategyDefinition(
            "strip",
            "Strip",
            "Buy one put and two calls at the reference strike for a downside-skewed "
            "move structure.",
            (
                StrategyLeg("PE", "BUY", 0),
                StrategyLeg("CE", "BUY", 0, 2),
            ),
        ),
        StrategyDefinition(
            "strap",
            "Strap",
            "Buy two calls and one put at the reference strike for an upside-skewed "
            "move structure.",
            (
                StrategyLeg("CE", "BUY", 0, 2),
                StrategyLeg("PE", "BUY", 0),
            ),
        ),
        StrategyDefinition(
            "inverse_iron_condor",
            "Inverse iron condor",
            "Buy the inner put and call wings and sell the outer wings for a "
            "defined-risk move strategy.",
            (
                StrategyLeg("PE", "SELL", -2),
                StrategyLeg("PE", "BUY", -1),
                StrategyLeg("CE", "BUY", 1),
                StrategyLeg("CE", "SELL", 2),
            ),
        ),
        StrategyDefinition(
            "guts",
            "Guts",
            "Buy an in-the-money call and put for a defined-risk volatility position.",
            (StrategyLeg("PE", "BUY", 1), StrategyLeg("CE", "BUY", -1)),
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
