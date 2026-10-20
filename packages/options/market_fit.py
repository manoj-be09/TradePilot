"""Read-only, deterministic NIFTY option-policy comparison.

This is an exposure-fit preview, not an executable recommendation or POP model.
"""

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from packages.market_data.contracts import OptionChain, OptionContractQuote
from packages.options.models import PayoffLeg, PayoffRequest
from packages.options.payoff import calculate_expiry_payoff
from packages.paper_trading.strategies import STRATEGY_REGISTRY
from packages.regime.classifier import MarketRegimeResult


class MarketFitLeg(BaseModel):
    option_type: Literal["CE", "PE"]
    side: Literal["BUY", "SELL"]
    strike: Decimal
    quantity: int
    entry_quote: Decimal
    bid: Decimal
    ask: Decimal


class MarketFitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain: OptionChain
    reference_strike: Decimal = Field(gt=0)


class MarketFitCandidate(BaseModel):
    strategy_key: str
    strategy_version: str
    name: str
    fit: Literal["MATCH", "UNKNOWN", "BLOCKED"]
    reasons: list[str]
    legs: list[MarketFitLeg]
    maximum_profit: Decimal | None = None
    maximum_loss: Decimal | None = None
    breakevens: list[Decimal] = Field(default_factory=list)
    provisional_stop: Decimal | None = None
    provisional_target: Decimal | None = None
    entry_policy: str
    exit_policy: str


class MarketFitAssessment(BaseModel):
    selector_version: str = "nifty-intraday-fit-v0-preview"
    chain_source: Literal["CLIENT_SUBMITTED_CHAIN"] = "CLIENT_SUBMITTED_CHAIN"
    payoff_basis: Literal["PER_OPTION_UNIT_BEFORE_COSTS"] = "PER_OPTION_UNIT_BEFORE_COSTS"
    chain_as_of: datetime
    reference_strike: Decimal | None = None
    chain_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    bar_source: Literal["UNAVAILABLE", "PROVIDER_HISTORICAL_HASH_ONLY"] = "UNAVAILABLE"
    bar_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    bar_count: int | None = Field(default=None, ge=1)
    regime_as_of: datetime | None
    regime: str
    action: Literal["NO_TRADE"] = "NO_TRADE"
    reasons: list[str]
    probability_of_profit: None = None
    candidates: list[MarketFitCandidate]


_POLICIES = (
    ("bull_call_spread", {"TRENDING_UP", "BREAKOUT"}),
    ("bear_put_spread", {"TRENDING_DOWN", "BREAKDOWN"}),
    ("iron_condor", {"RANGE_BOUND"}),
)
_ENTRY = (
    "Research recipe: one complete lot; 09:55–10:00 IST completed-bar decision, "
    "entry by 10:01 IST only on a later synchronized admissible basket. "
    "Listed expiry 5–10 calendar days away; selected-leg spread <=5%, "
    "exchange-book age <=2s and inter-leg skew <=1s. "
    "Costs, capital and all execution gates must be reviewed first."
)
_EXIT = (
    "Research recipe: check net liquidation every 5 minutes; stop at 25% "
    "of modeled expiry maximum loss, target at 25% of the smaller modeled "
    "maximum gain or loss; invalidate when the regime fit is lost. "
    "Send time-exit intent at 15:10 IST; a trigger never guarantees a fill."
)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _leg_quote(contract: OptionContractQuote, side: str) -> Decimal | None:
    quote = contract.quote
    if quote.bid is None or quote.ask is None or quote.bid <= 0 or quote.ask < quote.bid:
        return None
    return quote.ask if side == "BUY" else quote.bid


def assess_nifty_market_fit(
    chain: OptionChain,
    reference_strike: Decimal,
    regime: MarketRegimeResult | None,
    *,
    evaluated_at: datetime | None = None,
) -> MarketFitAssessment:
    now = evaluated_at or datetime.now(timezone.utc)
    reasons: list[str] = []
    if chain.underlying != "NIFTY":
        reasons.append("Only NIFTY is supported by this comparison.")
    if chain.as_of.tzinfo is None or now - chain.as_of > timedelta(minutes=5) or chain.as_of > now:
        reasons.append("Option-chain snapshot is not current and time-ordered.")
    if chain.quality is None or chain.quality.status != "PASS":
        reasons.append("Option-chain quality has not passed.")
    if regime is None:
        reasons.append("Complete, timestamped 5-minute index history is unavailable.")
    elif (
        regime.as_of.tzinfo is None
        or not timedelta(0) <= chain.as_of - regime.as_of <= timedelta(minutes=15)
    ):
        reasons.append("The completed-bar regime is stale or newer than the option-chain snapshot.")
        regime = None
    if not 5 <= (chain.expiry - now.astimezone(ZoneInfo("Asia/Kolkata")).date()).days <= 10:
        reasons.append("Expiry is outside the frozen 5–10 calendar-day research window.")
    strikes = sorted(
        {contract.strike for contract in chain.contracts if contract.expiry == chain.expiry}
    )
    spot = chain.underlying_spot
    if spot is None or not strikes:
        reasons.append("Spot or listed strikes are missing.")
    else:
        nearest = min(strikes, key=lambda strike: (abs(strike - spot), strike))
        if reference_strike != nearest:
            reasons.append("Reference strike is not the frozen nearest-to-spot strike.")
    input_blocks = reasons.copy()
    reasons.append("Policy-matched after-cost validation, capital and cost review are incomplete.")
    reasons.append("Exchange-origin executable-book timing and complete-basket fills are unproven.")
    candidates: list[MarketFitCandidate] = []
    for key, matching_regimes in _POLICIES:
        strategy = STRATEGY_REGISTRY.get(key)
        assert strategy is not None
        local_reasons: list[str] = input_blocks.copy()
        legs: list[MarketFitLeg] = []
        payoff_legs: list[PayoffLeg] = []
        book_times: list[datetime] = []
        if regime is None or regime.regime == "UNCERTAIN":
            local_reasons.append("Market regime is unknown.")
        elif regime.regime not in matching_regimes:
            local_reasons.append(f"{regime.regime} does not match this frozen exposure hypothesis.")
        if reference_strike not in strikes:
            local_reasons.append("Reference strike is not listed for this expiry.")
        else:
            index = strikes.index(reference_strike)
            for definition in strategy.legs:
                target_index = index + definition.strike_offset
                if target_index < 0 or target_index >= len(strikes):
                    local_reasons.append("A required adjacent strike is unavailable.")
                    break
                strike = strikes[target_index]
                contract = next(
                    (
                        item
                        for item in chain.contracts
                        if item.expiry == chain.expiry
                        and item.strike == strike
                        and item.option_type == definition.option_type
                    ),
                    None,
                )
                if contract is None:
                    local_reasons.append("A required contract is unavailable.")
                    break
                price = _leg_quote(contract, definition.side)
                if price is None or contract.quote.bid is None or contract.quote.ask is None:
                    local_reasons.append("A selected leg lacks a valid two-sided executable quote.")
                    break
                mid = (contract.quote.ask + contract.quote.bid) / 2
                if (contract.quote.ask - contract.quote.bid) / mid > Decimal("0.05"):
                    local_reasons.append("A selected leg exceeds the 5% bid/ask spread limit.")
                units = (
                    contract.quote.ask_quantity
                    if definition.side == "BUY"
                    else contract.quote.bid_quantity
                )
                if units is None or units < definition.quantity_ratio:
                    local_reasons.append(
                        "Displayed size for a selected leg is insufficient or unavailable."
                    )
                if (
                    contract.quote.book_timestamp_basis != "exchange"
                    or contract.quote.book_as_of is None
                ):
                    local_reasons.append("Exchange-origin book time is missing for a selected leg.")
                elif contract.quote.book_as_of.tzinfo is None or not timedelta(
                    0
                ) <= chain.as_of - contract.quote.book_as_of <= timedelta(seconds=2):
                    local_reasons.append("A selected leg has stale or future book time.")
                else:
                    book_times.append(contract.quote.book_as_of)
                quantity = definition.quantity_ratio
                legs.append(
                    MarketFitLeg(
                        option_type=definition.option_type,
                        side=definition.side,
                        strike=strike,
                        quantity=quantity,
                        entry_quote=price,
                        bid=contract.quote.bid,
                        ask=contract.quote.ask,
                    )
                )
                payoff_legs.append(
                    PayoffLeg(
                        option_type=definition.option_type,
                        strike=strike,
                        quantity=quantity if definition.side == "BUY" else -quantity,
                        entry_premium=price,
                    )
                )
        if book_times and max(book_times) - min(book_times) > timedelta(seconds=1):
            local_reasons.append("Selected legs exceed the one-second book-time skew limit.")
        maximum_profit: Decimal | None = None
        maximum_loss: Decimal | None = None
        breakevens: list[Decimal] = []
        stop: Decimal | None = None
        target: Decimal | None = None
        if len(payoff_legs) == len(strategy.legs):
            payoff = calculate_expiry_payoff(
                PayoffRequest(
                    underlying="NIFTY",
                    multiplier=Decimal(1),
                    price_min=Decimal(0),
                    price_max=max(strikes) * 2,
                    grid_points=2,
                    legs=payoff_legs,
                )
            )
            maximum_profit = payoff.maximum_profit
            maximum_loss = payoff.maximum_loss
            breakevens = payoff.breakeven_prices
            if (
                maximum_loss is None
                or maximum_loss <= 0
                or maximum_profit is None
                or maximum_profit <= 0
            ):
                local_reasons.append(
                    "Defined, positive expiry risk and reward were not established."
                )
            else:
                stop = _money(maximum_loss * Decimal("0.25"))
                target = _money(min(maximum_profit, maximum_loss) * Decimal("0.25"))
        fit: Literal["MATCH", "UNKNOWN", "BLOCKED"] = (
            "BLOCKED"
            if local_reasons
            and any(
                "does not match" not in text and "unknown" not in text for text in local_reasons
            )
            else "MATCH"
            if regime is not None and regime.regime in matching_regimes and not local_reasons
            else "UNKNOWN"
        )
        candidates.append(
            MarketFitCandidate(
                strategy_key=key,
                strategy_version=strategy.version,
                name=strategy.name,
                fit=fit,
                reasons=local_reasons,
                legs=legs,
                maximum_profit=maximum_profit,
                maximum_loss=maximum_loss,
                breakevens=breakevens,
                provisional_stop=stop,
                provisional_target=target,
                entry_policy=_ENTRY,
                exit_policy=_EXIT,
            )
        )
    candidates.sort(
        key=lambda item: (
            0 if item.fit == "MATCH" else 1 if item.fit == "UNKNOWN" else 2,
            item.strategy_key,
        )
    )
    return MarketFitAssessment(
        chain_as_of=chain.as_of,
        reference_strike=reference_strike,
        regime_as_of=regime.as_of if regime else None,
        regime=regime.regime if regime else "UNKNOWN",
        reasons=reasons,
        candidates=candidates,
    )
