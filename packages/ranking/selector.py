"""Rank NIFTY paper templates from specialist views, live quotes, costs and risk.

The selector builds every paper template at the reference strike from the current
chain, applies the same fail-closed quote, cost and account-risk gates for each, and
scores the survivors by specialist conviction and after-cost reward/risk. It proposes
at most one candidate; it never opens a position. Entries still go through the paper
entry handler's own checks.

Score = specialist weight x conviction x (0.5 + 0.5 x min(after-cost reward/risk, 3) / 3).
Reward is the expiry maximum profit, except for unlimited-upside structures, where it is
the better expiry payoff at spot +/- one standard deviation (ATM IV x sqrt(days / 365)),
so an uncapped payoff alone never earns the top score.
Weights default to 1 and are the input a later learning loop adjusts. Scores are
heuristic orderings, not probabilities or expected returns.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from packages.core.charges import order_charges, schedule_for
from packages.core.risk import AccountRiskState, EntryIntent, RiskLimits, evaluate_entry
from packages.market_data.contracts import OptionChain, OptionContractQuote
from packages.options.models import PayoffLeg, PayoffRequest
from packages.options.payoff import calculate_expiry_payoff
from packages.paper_trading.strategies import STRATEGY_REGISTRY, StrategyDefinition
from packages.ranking.specialists import (
    SpecialistView,
    Stance,
    VolatilityContext,
    atm_implied_volatility,
    specialist_views,
    stance_for,
)
from packages.regime.classifier import MarketRegimeResult

RANKING_VERSION = "nifty-specialists-v1"
MIN_SCORE = Decimal("0.25")
MAX_SPREAD_FRACTION = Decimal("0.05")
REWARD_RISK_CAP = Decimal(3)

_ZERO = Decimal(0)
_IST = ZoneInfo("Asia/Kolkata")
CandidateStatus = Literal["ELIGIBLE", "BLOCKED", "NO_VIEW"]
_TIMESTAMP_WARNINGS = {"SOURCE_TIMESTAMP_UNVERIFIED", "BOOK_TIMESTAMP_UNVERIFIED"}


class RankedLeg(BaseModel):
    option_type: Literal["CE", "PE"]
    side: Literal["BUY", "SELL"]
    strike: Decimal
    quantity_ratio: int
    price: Decimal
    bid: Decimal
    ask: Decimal


class RankedCandidate(BaseModel):
    strategy_key: str
    strategy_version: str
    name: str
    stance: Stance
    status: CandidateStatus
    score: Decimal
    conviction: Decimal
    reasons: list[str]
    legs: list[RankedLeg]
    net_premium_per_unit: Decimal | None = Field(
        default=None, description="Positive receives a credit; negative pays a debit."
    )
    maximum_profit_per_unit: Decimal | None = None
    maximum_profit_unbounded: bool = False
    maximum_loss_per_unit: Decimal | None = None
    breakevens: list[Decimal] = Field(default_factory=list)
    lot_size: int | None = None
    maximum_loss_per_lot: Decimal | None = None
    estimated_round_trip_costs_per_lot: Decimal | None = None
    cost_schedule_id: str | None = None
    costs_verified: bool = False
    reward_to_risk_after_costs: Decimal | None = None
    reward_basis: Literal["EXPIRY_MAXIMUM", "ONE_SIGMA_MOVE"] | None = None


class SpecialistSummary(BaseModel):
    stance: Stance
    conviction: Decimal
    weight: Decimal
    reasons: list[str]


class RankingResult(BaseModel):
    ranking_version: str = RANKING_VERSION
    evaluated_at: datetime
    chain_as_of: datetime
    reference_strike: Decimal
    expiry: str
    regime: str
    regime_reasons: list[str]
    atm_iv_pct: Decimal | None
    realized_vol_pct: Decimal | None
    specialists: list[SpecialistSummary]
    action: Literal["PAPER_CANDIDATE", "NO_TRADE"]
    selected_strategy_key: str | None
    reasons: list[str]
    probability_of_profit: None = None
    candidates: list[RankedCandidate]


@dataclass(frozen=True)
class RankingInputs:
    chain: OptionChain
    reference_strike: Decimal
    regime: MarketRegimeResult | None
    evaluated_at: datetime
    lot_size: int | None = None
    account: AccountRiskState | None = None
    limits: RiskLimits | None = None
    allow_unverified_book_time: bool = False
    weights: Mapping[Stance, Decimal] = field(default_factory=dict)


def _q(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _chain_blocks(inputs: RankingInputs) -> list[str]:
    chain, now = inputs.chain, inputs.evaluated_at
    blocks: list[str] = []
    if chain.underlying != "NIFTY":
        blocks.append("Only NIFTY is supported.")
    if chain.as_of.tzinfo is None or not timedelta(0) <= now - chain.as_of <= timedelta(minutes=5):
        blocks.append("Option-chain snapshot is not current and time-ordered.")
    quality = chain.quality
    timestamp_only_warning = (
        inputs.allow_unverified_book_time
        and quality is not None
        and quality.status == "WARN"
        and all(issue.code in _TIMESTAMP_WARNINGS for issue in quality.issues)
    )
    if quality is None or (quality.status != "PASS" and not timestamp_only_warning):
        blocks.append("Option-chain quality has not passed.")
    if chain.expiry <= now.astimezone(_IST).date():
        blocks.append("Expiry is today or past; same-day expiry entries are not ranked.")
    if inputs.regime is not None and (
        inputs.regime.as_of.tzinfo is None
        or not timedelta(0) <= chain.as_of - inputs.regime.as_of <= timedelta(minutes=15)
    ):
        blocks.append("The completed-bar regime is stale or newer than the option chain.")
    if inputs.lot_size is None or inputs.lot_size <= 0:
        blocks.append("Contract lot size is unavailable.")
    return blocks


def _payoff_at(legs: list[RankedLeg], price: Decimal) -> Decimal:
    """Per-unit expiry payoff at one underlying price, before costs."""
    total = _ZERO
    for leg in legs:
        moneyness = price - leg.strike if leg.option_type == "CE" else leg.strike - price
        intrinsic = max(_ZERO, moneyness)
        sign = 1 if leg.side == "BUY" else -1
        total += sign * leg.quantity_ratio * (intrinsic - leg.price)
    return total


def _one_sigma_move(
    chain: OptionChain, atm_iv_pct: Decimal | None, now: datetime
) -> Decimal | None:
    if atm_iv_pct is None or chain.underlying_spot is None:
        return None
    days = max(1, (chain.expiry - now.astimezone(_IST).date()).days)
    return chain.underlying_spot * atm_iv_pct / 100 * (Decimal(days) / 365).sqrt()


def _find(chain: OptionChain, strike: Decimal, option_type: str) -> OptionContractQuote | None:
    return next(
        (
            item for item in chain.contracts
            if item.expiry == chain.expiry and item.strike == strike
            and item.option_type == option_type
        ),
        None,
    )


def _finish(candidate: RankedCandidate, reasons: list[str]) -> RankedCandidate:
    # Pydantic copies lists on construction; publish the final reasons explicitly.
    candidate.reasons = reasons
    return candidate


def _build_candidate(
    strategy: StrategyDefinition,
    stance: Stance,
    view: SpecialistView,
    weight: Decimal,
    inputs: RankingInputs,
    strikes: list[Decimal],
    global_blocks: list[str],
    sigma_move: Decimal | None,
) -> RankedCandidate:
    chain = inputs.chain
    reasons = list(global_blocks)
    legs: list[RankedLeg] = []
    payoff_legs: list[PayoffLeg] = []
    book_times: list[datetime] = []
    lot = inputs.lot_size if inputs.lot_size and inputs.lot_size > 0 else None
    index = strikes.index(inputs.reference_strike)
    for definition in strategy.legs:
        target = index + definition.strike_offset
        if not 0 <= target < len(strikes):
            reasons.append("A required strike is not listed.")
            break
        contract = _find(chain, strikes[target], definition.option_type)
        quote = contract.quote if contract else None
        if (contract is None or quote is None or quote.bid is None or quote.ask is None
                or quote.bid <= 0 or quote.ask < quote.bid):
            reasons.append(f"{strikes[target]} {definition.option_type} lacks a two-sided quote.")
            break
        mid = (quote.ask + quote.bid) / 2
        if (quote.ask - quote.bid) / mid > MAX_SPREAD_FRACTION:
            reasons.append(f"{strikes[target]} {definition.option_type} spread exceeds 5%.")
        # Same source as the paper fill check: contract-level displayed size first.
        displayed = (
            (contract.ask_quantity if contract.ask_quantity is not None else quote.ask_quantity)
            if definition.side == "BUY"
            else (contract.bid_quantity if contract.bid_quantity is not None
                  else quote.bid_quantity)
        )
        needed = definition.quantity_ratio * (lot or 1)
        if displayed is None or displayed < needed:
            reasons.append(
                f"Displayed size for {strikes[target]} {definition.option_type} is insufficient."
            )
        if not inputs.allow_unverified_book_time:
            if quote.book_timestamp_basis != "exchange" or quote.book_as_of is None:
                reasons.append("Exchange-origin book time is missing for a selected leg.")
            elif quote.book_as_of.tzinfo is None or not timedelta(0) <= (
                chain.as_of - quote.book_as_of
            ) <= timedelta(seconds=2):
                reasons.append("A selected leg has stale or future book time.")
            else:
                book_times.append(quote.book_as_of)
        price = quote.ask if definition.side == "BUY" else quote.bid
        legs.append(RankedLeg(
            option_type=definition.option_type, side=definition.side, strike=strikes[target],
            quantity_ratio=definition.quantity_ratio, price=price, bid=quote.bid, ask=quote.ask,
        ))
        payoff_legs.append(PayoffLeg(
            option_type=definition.option_type, strike=strikes[target],
            quantity=definition.quantity_ratio * (1 if definition.side == "BUY" else -1),
            entry_premium=price,
        ))
    if book_times and max(book_times) - min(book_times) > timedelta(seconds=1):
        reasons.append("Selected legs exceed the one-second book-time skew limit.")

    candidate = RankedCandidate(
        strategy_key=strategy.key, strategy_version=strategy.version, name=strategy.name,
        stance=stance, status="BLOCKED", score=_ZERO, conviction=view.conviction,
        reasons=reasons, legs=legs, lot_size=lot,
    )
    if len(payoff_legs) != len(strategy.legs):
        return _finish(candidate, reasons)

    payoff = calculate_expiry_payoff(PayoffRequest(
        underlying="NIFTY", multiplier=Decimal(1), price_min=_ZERO,
        price_max=max(strikes) * 2, grid_points=2, legs=payoff_legs,
    ))
    net_premium = sum(
        (leg.price * leg.quantity_ratio * (1 if leg.side == "SELL" else -1) for leg in legs),
        _ZERO,
    )
    candidate.net_premium_per_unit = _q(net_premium)
    candidate.maximum_profit_per_unit = payoff.maximum_profit
    candidate.maximum_profit_unbounded = payoff.maximum_profit_unbounded
    candidate.maximum_loss_per_unit = payoff.maximum_loss
    candidate.breakevens = payoff.breakeven_prices
    if payoff.maximum_loss_unbounded or payoff.maximum_loss is None or payoff.maximum_loss <= 0:
        reasons.append("Defined, positive maximum loss was not established.")
        return _finish(candidate, reasons)
    if not payoff.maximum_profit_unbounded and (
        payoff.maximum_profit is None or payoff.maximum_profit <= 0
    ):
        reasons.append("The structure has no positive modeled profit.")
        return _finish(candidate, reasons)
    if lot is None:
        return _finish(candidate, reasons)

    trade_date = inputs.evaluated_at.astimezone(_IST).date()
    schedule = schedule_for(trade_date)
    entry_costs = exit_costs = _ZERO
    verified = True
    for leg in legs:
        units = leg.quantity_ratio * lot
        exit_side: Literal["BUY", "SELL"] = "SELL" if leg.side == "BUY" else "BUY"
        for side, bucket in ((leg.side, "entry"), (exit_side, "exit")):
            charge = order_charges(
                instrument="OPT", side=side, price=leg.price, quantity=units,
                trade_date=trade_date, schedule=schedule,
            )
            verified = verified and charge.verified
            if bucket == "entry":
                entry_costs += charge.total
            else:
                exit_costs += charge.total
    costs = entry_costs + exit_costs
    max_loss_lot = _q(payoff.maximum_loss * lot)
    candidate.maximum_loss_per_lot = max_loss_lot
    candidate.estimated_round_trip_costs_per_lot = _q(costs)
    candidate.cost_schedule_id = schedule.schedule_id
    candidate.costs_verified = verified

    reward_risk: Decimal | None
    if payoff.maximum_profit_unbounded or payoff.maximum_profit is None:
        spot = chain.underlying_spot
        if sigma_move is None or spot is None:
            reward_risk = None
            reasons.append("Unlimited-upside reward needs ATM IV and spot for a one-sigma move.")
        else:
            reward = max(_payoff_at(legs, spot + sigma_move), _payoff_at(legs, spot - sigma_move))
            reward_risk = (reward * lot - costs) / (max_loss_lot + costs)
            candidate.reward_basis = "ONE_SIGMA_MOVE"
    else:
        reward_risk = (payoff.maximum_profit * lot - costs) / (max_loss_lot + costs)
        candidate.reward_basis = "EXPIRY_MAXIMUM"
    if reward_risk is not None:
        candidate.reward_to_risk_after_costs = _q(min(reward_risk, REWARD_RISK_CAP), "0.0001")
        if reward_risk <= 0:
            reasons.append("Estimated costs exceed the modeled reward.")

    if inputs.account is not None and inputs.limits is not None:
        decision = evaluate_entry(
            EntryIntent(
                max_loss=max_loss_lot + _q(costs),
                entry_cash_flow=_q(net_premium * lot - entry_costs),
            ),
            inputs.account, inputs.limits,
        )
        reasons.extend(rejection.message for rejection in decision.rejections)
    else:
        reasons.append("Paper account risk state is unavailable.")

    if view.conviction <= 0:
        candidate.status = "NO_VIEW"
        reasons.extend(view.reasons)
        return _finish(candidate, reasons)
    candidate.score = _q(
        weight * view.conviction
        * (Decimal("0.5") + Decimal("0.5") * min(max(reward_risk or _ZERO, _ZERO), REWARD_RISK_CAP)
           / REWARD_RISK_CAP),
        "0.0001",
    )
    if not reasons:
        candidate.status = "ELIGIBLE"
    return _finish(candidate, reasons)


_STATUS_ORDER = {"ELIGIBLE": 0, "BLOCKED": 1, "NO_VIEW": 2}


def rank_nifty_strategies(inputs: RankingInputs) -> RankingResult:
    chain = inputs.chain
    regime = inputs.regime
    vol = VolatilityContext(
        atm_iv_pct=atm_implied_volatility(chain, inputs.reference_strike),
        realized_vol_pct=regime.realized_volatility_annualized_percent if regime else None,
    )
    views = {view.stance: view for view in specialist_views(regime, vol)}
    weights = {stance: inputs.weights.get(stance, Decimal(1)) for stance in views}
    strikes = sorted({c.strike for c in chain.contracts if c.expiry == chain.expiry})
    global_blocks = _chain_blocks(inputs)
    sigma_move = _one_sigma_move(chain, vol.atm_iv_pct, inputs.evaluated_at)
    reasons = list(global_blocks)
    if inputs.allow_unverified_book_time:
        reasons.append(
            "Book time is not exchange-verified; allowed by the local paper quote setting."
        )

    candidates: list[RankedCandidate] = []
    if inputs.reference_strike not in strikes:
        reasons.append("Reference strike is not listed for this expiry.")
    else:
        for strategy in STRATEGY_REGISTRY.list():
            stance = stance_for(strategy.key)
            if stance is None:
                continue
            candidates.append(_build_candidate(
                strategy, stance, views[stance], weights[stance], inputs, strikes, global_blocks,
                sigma_move,
            ))
    candidates.sort(key=lambda item: (_STATUS_ORDER[item.status], -item.score, item.strategy_key))

    best = next((item for item in candidates if item.status == "ELIGIBLE"), None)
    if best is not None and best.score < MIN_SCORE:
        reasons.append(
            f"The best eligible score {best.score} is below the {MIN_SCORE} threshold."
        )
        best = None
    elif best is None and candidates:
        reasons.append("No template passed every quote, cost and risk gate.")
    if best is None:
        reasons.append("Decision: no trade.")
    return RankingResult(
        evaluated_at=inputs.evaluated_at,
        chain_as_of=chain.as_of,
        reference_strike=inputs.reference_strike,
        expiry=chain.expiry.isoformat(),
        regime=regime.regime if regime else "UNKNOWN",
        regime_reasons=list(regime.reasons) if regime else [],
        atm_iv_pct=vol.atm_iv_pct,
        realized_vol_pct=vol.realized_vol_pct,
        specialists=[
            SpecialistSummary(
                stance=view.stance, conviction=view.conviction, weight=weights[view.stance],
                reasons=list(view.reasons),
            )
            for view in views.values()
        ],
        action="PAPER_CANDIDATE" if best else "NO_TRADE",
        selected_strategy_key=best.strategy_key if best else None,
        reasons=reasons,
        candidates=candidates,
    )
