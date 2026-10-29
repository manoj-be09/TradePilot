"""Deterministic market specialists for NIFTY option ranking.

Each specialist reads the same completed-bar regime and option-chain volatility
context and states how strongly current conditions suit its exposure family.
Convictions are heuristic weights in [0, 1], not probabilities of profit, and the
same inputs always produce the same views.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from packages.market_data.contracts import OptionChain
from packages.regime.classifier import MarketRegimeResult

Stance = Literal["BULLISH", "BEARISH", "RANGE", "VOLATILITY"]
STANCES: tuple[Stance, ...] = ("BULLISH", "BEARISH", "RANGE", "VOLATILITY")

# Every paper template belongs to exactly one specialist (checked by tests).
STANCE_STRATEGIES: dict[Stance, tuple[str, ...]] = {
    "BULLISH": (
        "bull_call_spread", "bull_put_spread", "long_call", "put_broken_wing", "strap",
    ),
    "BEARISH": (
        "bear_put_spread", "bear_call_spread", "long_put", "call_broken_wing", "strip",
    ),
    "RANGE": (
        "iron_condor", "iron_butterfly", "long_call_butterfly", "long_put_butterfly",
        "long_call_condor", "long_put_condor",
    ),
    "VOLATILITY": (
        "long_straddle", "long_strangle", "inverse_iron_condor", "short_call_butterfly",
        "short_put_butterfly", "short_call_condor", "short_put_condor",
        "call_ratio_backspread", "put_ratio_backspread", "guts",
    ),
}

_ZERO = Decimal(0)
_ONE = Decimal(1)


def stance_for(strategy_key: str) -> Stance | None:
    for stance, keys in STANCE_STRATEGIES.items():
        if strategy_key in keys:
            return stance
    return None


@dataclass(frozen=True)
class VolatilityContext:
    """Annualized percentages: ATM implied (from the chain) and realized (from bars)."""

    atm_iv_pct: Decimal | None
    realized_vol_pct: Decimal | None

    @property
    def iv_to_rv(self) -> Decimal | None:
        if self.atm_iv_pct is None or not self.realized_vol_pct:
            return None
        return self.atm_iv_pct / self.realized_vol_pct


@dataclass(frozen=True)
class SpecialistView:
    stance: Stance
    conviction: Decimal
    reasons: tuple[str, ...]


def atm_implied_volatility(chain: OptionChain, reference_strike: Decimal) -> Decimal | None:
    """Mean provider-reported IV of the reference-strike call and put, when both exist."""
    values = [
        contract.implied_volatility
        for contract in chain.contracts
        if contract.expiry == chain.expiry
        and contract.strike == reference_strike
        and contract.implied_volatility is not None
        and contract.implied_volatility > 0
    ]
    if len(values) != 2:
        return None
    return sum(values, _ZERO) / 2


def _clamp(value: Decimal) -> Decimal:
    return min(_ONE, max(_ZERO, value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _trend_strength(regime: MarketRegimeResult) -> Decimal:
    """0.6 at ADX 25, rising to 1.0 by ADX 45; breakouts without ADX use 0.6."""
    if regime.adx_14 is None:
        return Decimal("0.6")
    return _clamp(Decimal("0.6") + (regime.adx_14 - 25) / 50)


def _directional(
    stance: Literal["BULLISH", "BEARISH"], regime: MarketRegimeResult
) -> SpecialistView:
    trend, breakout, bias = (
        ("TRENDING_UP", "BREAKOUT", "UP") if stance == "BULLISH"
        else ("TRENDING_DOWN", "BREAKDOWN", "DOWN")
    )
    if regime.regime in (trend, breakout):
        return SpecialistView(stance, _trend_strength(regime), (
            f"{regime.regime} supports {stance.lower()} exposure.",
        ))
    if regime.directional_bias == bias:
        return SpecialistView(stance, Decimal("0.3"), (
            f"Directional bias is {bias} without a confirmed trend.",
        ))
    return SpecialistView(stance, _ZERO, (f"{regime.regime} does not support this view.",))


def _range(regime: MarketRegimeResult, vol: VolatilityContext) -> SpecialistView:
    if regime.regime == "RANGE_BOUND":
        conviction, reasons = Decimal("0.6"), ["ADX shows a range without a breakout."]
    elif regime.regime == "LOW_VOLATILITY":
        conviction, reasons = Decimal("0.4"), ["Realized volatility is in its low band."]
    else:
        return SpecialistView("RANGE", _ZERO, (f"{regime.regime} does not support a range.",))
    ratio = vol.iv_to_rv
    if ratio is None:
        reasons.append("Implied/realized volatility comparison is unavailable.")
    elif ratio >= Decimal("1.1"):
        conviction += Decimal("0.2")
        reasons.append(f"Options are rich: ATM IV is {ratio:.2f}x realized volatility.")
    elif ratio < 1:
        conviction -= Decimal("0.2")
        reasons.append(f"Options are cheap: ATM IV is {ratio:.2f}x realized volatility.")
    return SpecialistView("RANGE", _clamp(conviction), tuple(reasons))


def _volatility(regime: MarketRegimeResult, vol: VolatilityContext) -> SpecialistView:
    ratio = vol.iv_to_rv
    if regime.regime == "LOW_VOLATILITY":
        conviction, reasons = Decimal("0.3"), ["Compressed realized volatility can expand."]
    elif regime.regime == "HIGH_VOLATILITY":
        conviction, reasons = Decimal("0.4"), ["Realized volatility is in its high band."]
    elif regime.regime in ("BREAKOUT", "BREAKDOWN"):
        conviction, reasons = Decimal("0.3"), [f"{regime.regime} can extend into a large move."]
    else:
        return SpecialistView("VOLATILITY", _ZERO, (
            f"{regime.regime} does not point to a large move.",
        ))
    if ratio is None:
        reasons.append("Implied/realized volatility comparison is unavailable.")
    elif ratio <= 1:
        conviction += Decimal("0.2")
        reasons.append(f"Options are cheap: ATM IV is {ratio:.2f}x realized volatility.")
    elif ratio >= Decimal("1.3"):
        conviction -= Decimal("0.3")
        reasons.append(f"Options are expensive: ATM IV is {ratio:.2f}x realized volatility.")
    return SpecialistView("VOLATILITY", _clamp(conviction), tuple(reasons))


def specialist_views(
    regime: MarketRegimeResult | None, vol: VolatilityContext
) -> tuple[SpecialistView, ...]:
    """One view per stance, in STANCES order. Unknown regimes give zero conviction."""
    if regime is None or regime.regime == "UNCERTAIN":
        reason = (
            "Market regime is unknown." if regime is None
            else "The classifier leaves the regime uncertain."
        )
        return tuple(SpecialistView(stance, _ZERO, (reason,)) for stance in STANCES)
    return (
        _directional("BULLISH", regime),
        _directional("BEARISH", regime),
        _range(regime, vol),
        _volatility(regime, vol),
    )


def mapped_keys() -> Iterable[str]:
    for keys in STANCE_STRATEGIES.values():
        yield from keys
