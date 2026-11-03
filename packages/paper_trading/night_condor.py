"""Live decisions for the night condors under the owner's override. Simulated paper only.

The research functions in rich_premium.py run unchanged on live data, so the live rule is the
frozen one: past sessions' 5-minute candles give the forecast, and the option chain's last prices at
the decision stand in for the bars closed by then. The override skips the rule engine's promotion
bar (owner's decision, 3 November 2026), so every trade is named "not promoted", and a version the
engine has demoted, retired or replaced is never traded.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from packages.backtesting.published_strategies import _BAR, Bar, Book, Kind
from packages.backtesting.rich_premium import (
    _FORECAST_SESSIONS,
    SessionMove,
    condor_strikes,
    is_before_break,
    read_richness,
)
from packages.backtesting.rule_engine import EngineRule
from packages.market_data.contracts import OptionChain
from packages.paper_trading.option_policy import NEXT_SESSION_POLICY_VERSION, OptionExitPolicy

# 20 minutes before the close is 15:10, the research exit bar on a normal session.
NIGHT_EXIT_POLICY = OptionExitPolicy(20, NEXT_SESSION_POLICY_VERSION)
STOPPED_STAGES = frozenset({"DEMOTED", "RETIRED", "SUPERSEDED"})


@dataclass(frozen=True)
class NightDecision:
    # Sold call, sold put, bought call, bought put; None means no trade today.
    strikes: list[Decimal] | None
    richness: float | None
    reason: str


def chain_book(chain: OptionChain, decided_at: datetime, sessions: Sequence[date]) -> Book:
    """Last prices as bars closed at the decision; `sessions` (today to expiry) only count time."""
    moment = decided_at - _BAR
    bars: dict[tuple[Decimal, Kind], dict[datetime, Bar]] = {}
    for contract in chain.contracts:
        price = float(contract.quote.last_price)
        if contract.option_type in ("CE", "PE") and price > 0:
            kind: Kind = "CE" if contract.option_type == "CE" else "PE"
            bars[(contract.strike, kind)] = {moment: Bar(price, price, price, price)}
    days: dict[date, list[datetime]] = {day: [] for day in sessions}
    days[decided_at.date()] = [moment]
    return Book(chain.expiry, bars, days)


def decide(
    rule: EngineRule, past: Sequence[SessionMove], today: Book, decided_at: datetime,
    previous: date, following: date | None,
) -> NightDecision:
    """The frozen rule on one live session; fails closed whenever its inputs are incomplete."""
    params = rule.params
    if len(past) < _FORECAST_SESSIONS or past[-1].session != previous:
        return NightDecision(None, None, "Past sessions are incomplete; no trade")
    if is_before_break(today, decided_at.date(), following):
        return NightDecision(None, None, "The session before a break; no trade")
    richness = read_richness(today, decided_at, past[-_FORECAST_SESSIONS:])
    if richness is None:
        return NightDecision(None, None, "Richness could not be read; no trade")
    if richness < params["threshold"]:
        return NightDecision(None, richness,
                             f"Richness {richness:.2f} is below {params['threshold']}; no trade")
    strikes = condor_strikes(today, decided_at, params["short_delta"])
    if strikes is None:
        return NightDecision(None, richness, "No strikes near the target delta; no trade")
    return NightDecision(strikes, richness, f"Richness {richness:.2f}; condor chosen")
