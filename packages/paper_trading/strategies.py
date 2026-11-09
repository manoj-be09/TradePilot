from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

OptionType = Literal["CE", "PE"]
TradeSide = Literal["BUY", "SELL"]
ExpiryScope = Literal["SAME_EXPIRY", "MULTI_EXPIRY"]


@dataclass(frozen=True)
class StrategyLeg:
    option_type: OptionType
    side: TradeSide
    strike_offset: int
    quantity_ratio: int = 1
    expiry_offset: int = 0  # 0: the trade's expiry; 1: the next listed expiry after it

    def __post_init__(self) -> None:
        if self.quantity_ratio < 1:
            raise ValueError("Strategy leg quantity ratio must be at least one")
        if self.expiry_offset not in (0, 1):
            raise ValueError("A strategy leg may only use the trade's or the next expiry")


@dataclass(frozen=True)
class StrategyDefinition:
    key: str
    name: str
    description: str
    legs: tuple[StrategyLeg, ...]
    version: str = "1.0.0"
    risk_profile: str = "DEFINED_RISK"
    lifecycle_status: str = "PAPER_TEMPLATE"
    expiry_scope: ExpiryScope = "SAME_EXPIRY"


class StrategyRegistry:
    """Immutable definitions for strategies available to paper trading."""

    def __init__(self, strategies: tuple[StrategyDefinition, ...]) -> None:
        by_key = {strategy.key: strategy for strategy in strategies}
        if len(by_key) != len(strategies):
            raise ValueError("Strategy keys must be unique")
        if any(not strategy.legs for strategy in strategies):
            raise ValueError("Every strategy must define at least one option leg")
        for strategy in strategies:
            multi = any(leg.expiry_offset for leg in strategy.legs)
            if multi != (strategy.expiry_scope == "MULTI_EXPIRY"):
                raise ValueError(f"Strategy {strategy.key!r} has an inconsistent expiry scope")
            if multi and not _is_long_calendar(strategy):
                raise ValueError(
                    f"Multi-expiry strategy {strategy.key!r} must be a long calendar or "
                    "a diagonal with its bought leg on the protected side"
                )
            if strategy.risk_profile == "DEFINED_RISK" and _call_tail_slope(strategy) < 0:
                raise ValueError(
                    f"Defined-risk strategy {strategy.key!r} has unbounded call-side loss"
                )
        self._strategies = strategies
        self._by_key = by_key

    def list(self) -> tuple[StrategyDefinition, ...]:
        """Same-expiry templates: every backtest, ranking and research path assumes one expiry."""
        return tuple(item for item in self._strategies if item.expiry_scope == "SAME_EXPIRY")

    def calendars(self) -> tuple[StrategyDefinition, ...]:
        """Multi-expiry templates; only paper entry, exits and the forward runner handle them."""
        return tuple(item for item in self._strategies if item.expiry_scope == "MULTI_EXPIRY")

    def get(self, key: str) -> StrategyDefinition | None:
        return self._by_key.get(key)


def _is_long_calendar(strategy: StrategyDefinition) -> bool:
    """Only near options sold and later options bought, matched per option type, with each
    bought strike on the protected side: calls at or below the sold strikes, puts at or above.

    That keeps the loss at the near expiry bounded; calendar_max_loss gives the bound.
    """
    if any((leg.side == "SELL") != (leg.expiry_offset == 0) for leg in strategy.legs):
        return False
    for option_type in ("CE", "PE"):
        near = [leg for leg in strategy.legs
                if leg.option_type == option_type and leg.expiry_offset == 0]
        far = [leg for leg in strategy.legs
               if leg.option_type == option_type and leg.expiry_offset == 1]
        if sum(leg.quantity_ratio for leg in near) != sum(leg.quantity_ratio for leg in far):
            return False
        if near and option_type == "CE" and (
                max(leg.strike_offset for leg in far) > min(leg.strike_offset for leg in near)):
            return False
        if near and option_type == "PE" and (
                min(leg.strike_offset for leg in far) < max(leg.strike_offset for leg in near)):
            return False
    return True


# A later put may trade below intrinsic by up to strike x rate x time; 10% a year is above
# any Indian policy rate this system has seen, so the allowance errs on the side of loss.
_PUT_RATE = Decimal("0.10")


def calendar_max_loss(
    legs: Iterable[tuple[str, Decimal, int, bool]], cash_flow: Decimal, multiplier: int,
    days_between_expiries: int,
) -> Decimal:
    """Worst loss at the near expiry for a calendar or diagonal the registry allows.

    legs are (option type, strike, signed lots, on the later expiry). Near options are
    valued at intrinsic. A later call is taken at its intrinsic value and a later put at
    the strike discounted at _PUT_RATE less spot, the lowest each may trade at; the loss
    is the debit paid less the lowest such value over every spot price.
    """
    legs = list(legs)
    discount = 1 / (1 + _PUT_RATE * days_between_expiries / 365)

    def value(spot: Decimal) -> Decimal:
        total = Decimal(0)
        for option_type, strike, lots, later in legs:
            if option_type == "CE":
                total += lots * max(Decimal(0), spot - strike)
            else:
                total += lots * max(Decimal(0), strike * (discount if later else 1) - spot)
        return total

    if sum(lots for option_type, _, lots, _ in legs if option_type == "CE") < 0:
        raise ValueError("A calendar may not be short calls overall")
    # Value is piecewise linear in spot, so its lowest point is at zero or a kink.
    kinks = {Decimal(0)} | {strike for _, strike, _, _ in legs} | {
        strike * discount for option_type, strike, _, later in legs
        if option_type == "PE" and later}
    return -cash_flow - min(value(spot) for spot in kinks) * multiplier


def _call_tail_slope(strategy: StrategyDefinition) -> int:
    """Return the signed call exposure as spot tends to infinity.

    A positive BUY call ratio contributes +1 and a SELL call ratio contributes -1.
    Any negative total leaves a naked short-call tail and therefore cannot be
    advertised as a bounded-loss paper template. Put legs do not create an
    unbounded loss for a non-negative underlying price.
    """

    return sum(
        leg.quantity_ratio if leg.side == "BUY" else -leg.quantity_ratio
        for leg in strategy.legs
        if leg.option_type == "CE"
    )


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
            "range_iron_condor",
            "Range iron condor",
            "Sell the put and call six strikes out, protected six strikes further on each "
            "side, for a range-bound market with rich option prices.",
            (
                StrategyLeg("PE", "BUY", -12),
                StrategyLeg("PE", "SELL", -6),
                StrategyLeg("CE", "SELL", 6),
                StrategyLeg("CE", "BUY", 12),
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
            "short_call_condor",
            "Short call condor",
            "Sell the outer calls and buy the two inner calls for a bounded move-seeking range.",
            (
                StrategyLeg("CE", "SELL", -2),
                StrategyLeg("CE", "BUY", -1),
                StrategyLeg("CE", "BUY", 1),
                StrategyLeg("CE", "SELL", 2),
            ),
        ),
        StrategyDefinition(
            "short_put_condor",
            "Short put condor",
            "Sell the outer puts and buy the two inner puts for a bounded move-seeking range.",
            (
                StrategyLeg("PE", "SELL", -2),
                StrategyLeg("PE", "BUY", -1),
                StrategyLeg("PE", "BUY", 1),
                StrategyLeg("PE", "SELL", 2),
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
            "Buy two puts and one call at the reference strike for a downside-skewed "
            "move structure.",
            (
                StrategyLeg("PE", "BUY", 0, 2),
                StrategyLeg("CE", "BUY", 0),
            ),
            version="2.0.0",
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
            "put_broken_wing",
            "Put broken wing",
            "Buy one higher-strike put, sell two middle puts, and buy a wider "
            "lower-strike put wing.",
            (
                StrategyLeg("PE", "BUY", 1),
                StrategyLeg("PE", "SELL", 0, 2),
                StrategyLeg("PE", "BUY", -2),
            ),
        ),
        StrategyDefinition(
            "call_broken_wing",
            "Call broken wing",
            "Buy one lower-strike call, sell two middle calls, and buy a wider "
            "higher-strike call wing.",
            (
                StrategyLeg("CE", "BUY", -1),
                StrategyLeg("CE", "SELL", 0, 2),
                StrategyLeg("CE", "BUY", 2),
            ),
        ),
        StrategyDefinition(
            "call_ratio_backspread",
            "Call ratio backspread",
            "Sell one call and buy two higher-strike calls for a bounded-loss "
            "upside move structure.",
            (
                StrategyLeg("CE", "SELL", 0),
                StrategyLeg("CE", "BUY", 1, 2),
            ),
        ),
        StrategyDefinition(
            "put_ratio_backspread",
            "Put ratio backspread",
            "Sell one put and buy two lower-strike puts for a bounded-loss "
            "downside move structure.",
            (
                StrategyLeg("PE", "SELL", 0),
                StrategyLeg("PE", "BUY", -1, 2),
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
            "inverse_iron_butterfly",
            "Inverse iron butterfly",
            "Buy the reference-strike put and call and sell the wings two strikes out for a "
            "defined-risk move strategy.",
            (
                StrategyLeg("PE", "SELL", -2),
                StrategyLeg("PE", "BUY", 0),
                StrategyLeg("CE", "BUY", 0),
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
        StrategyDefinition(
            "call_calendar",
            "Call calendar",
            "Sell the reference-strike call on this expiry and buy the same call on the next "
            "expiry. Maximum loss is the debit paid.",
            (StrategyLeg("CE", "SELL", 0), StrategyLeg("CE", "BUY", 0, expiry_offset=1)),
            expiry_scope="MULTI_EXPIRY",
        ),
        StrategyDefinition(
            "calendar_put_spread",
            "Put calendar",
            "Sell the reference-strike put on this expiry and buy the same put on the next "
            "expiry.",
            (StrategyLeg("PE", "SELL", 0), StrategyLeg("PE", "BUY", 0, expiry_offset=1)),
            expiry_scope="MULTI_EXPIRY",
        ),
        StrategyDefinition(
            "diagonal_call_spread",
            "Call diagonal",
            "Sell the call two strikes above on this expiry and buy the reference-strike call "
            "on the next expiry.",
            (StrategyLeg("CE", "SELL", 2), StrategyLeg("CE", "BUY", 0, expiry_offset=1)),
            expiry_scope="MULTI_EXPIRY",
        ),
        StrategyDefinition(
            "diagonal_put_spread",
            "Put diagonal",
            "Sell the put two strikes below on this expiry and buy the reference-strike put "
            "on the next expiry.",
            (StrategyLeg("PE", "SELL", -2), StrategyLeg("PE", "BUY", 0, expiry_offset=1)),
            expiry_scope="MULTI_EXPIRY",
        ),
        StrategyDefinition(
            "double_diagonal",
            "Double diagonal",
            "Sell the put and call three strikes out on this expiry and buy the put and call "
            "one strike out on the next expiry.",
            (
                StrategyLeg("PE", "SELL", -3),
                StrategyLeg("PE", "BUY", -1, expiry_offset=1),
                StrategyLeg("CE", "BUY", 1, expiry_offset=1),
                StrategyLeg("CE", "SELL", 3),
            ),
            expiry_scope="MULTI_EXPIRY",
        ),
    )
)
STRATEGIES = STRATEGY_REGISTRY.list()


def get_strategy(key: str) -> StrategyDefinition | None:
    return STRATEGY_REGISTRY.get(key)
