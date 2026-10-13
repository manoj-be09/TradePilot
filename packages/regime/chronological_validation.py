"""On-demand chronological review of fixed equity rules; never tunes or trades."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from packages.backtesting.performance import PerformanceTrade, summarize
from packages.market_data.contracts import HistoricalBar
from packages.paper_trading.equity import STRATEGIES, ZONE, replay_events
from packages.paper_trading.equity import VERSION as RULE_VERSION

MIN_BARS = 400
MIN_SPAN_DAYS = 730
MIN_TRAIN_TRADES = 20
MIN_VALIDATION_TRADES = 20
VERSION = "equity-chronological-shadow-v1"


class ValidationRow(BaseModel):
    strategy: str
    name: str
    status: Literal["insufficient", "reviewable"]
    training_trades: int
    validation_trades: int
    boundary_trades_excluded: int
    training_smoothed_win_pct: float | None = None
    validation_win_pct: float | None = None
    validation_average_net_pnl: float | None = None
    validation_brier: float | None = None


class ChronologicalValidation(BaseModel):
    status: Literal["ready", "unavailable"]
    reason: str | None = None
    provider: str
    symbol: str
    version: str = VERSION
    rule_version: str | None = None
    study_id: UUID | None = None
    generation: int = Field(default=1, ge=1)
    parent_study_id: UUID | None = None
    frozen_at: datetime | None = None
    frozen: bool = False
    dataset_digest: str | None = None
    allocation: Decimal | None = None
    planned_risk: Decimal | None = None
    history_from: date | None = None
    history_to: date | None = None
    training_from: date | None = None
    training_to: date | None = None
    validation_from: date | None = None
    validation_to: date | None = None
    reserved_from: date | None = None
    reserved_to: date | None = None
    bars: int = 0
    reserved_bars: int = 0
    rows: list[ValidationRow] = Field(default_factory=list)
    ranking_changed: bool = False
    parameters_changed: bool = False
    method: str = (
        "Fixed rules are replayed only through the training and validation windows. "
        "The latest 20% of sessions are reserved and their trade outcomes are not evaluated. "
        "Trades crossing a window boundary are excluded from that window's statistics. "
        "At least 20 closed trades in each earlier window are required to show comparison "
        "rates. This is an exploratory chronological check, not a calibrated probability "
        "or authorization to change paper risk parameters."
    )


def unavailable_validation(
    provider: str, symbol: str, reason: str
) -> ChronologicalValidation:
    return ChronologicalValidation(
        status="unavailable", provider=provider, symbol=symbol, reason=reason
    )


def review_fixed_equity_rules(
    bars: list[HistoricalBar], *, provider: str, symbol: str,
    budget: Decimal, risk_budget: Decimal,
) -> ChronologicalValidation:
    """Review fixed rules without inspecting outcomes in the reserved latest window."""
    if len(bars) < MIN_BARS:
        return unavailable_validation(
            provider, symbol, "At least 400 completed daily bars are needed."
        )
    if any(bar.timestamp.tzinfo is None for bar in bars):
        return unavailable_validation(
            provider, symbol, "Daily history lacks exchange-aware timestamps."
        )
    days = [bar.timestamp.astimezone(ZONE).date() for bar in bars]
    if any(left >= right for left, right in zip(days, days[1:], strict=False)):
        return unavailable_validation(provider, symbol, "Daily history is duplicated or unordered.")
    if (days[-1] - days[0]).days < MIN_SPAN_DAYS:
        return unavailable_validation(provider, symbol, "Daily history covers less than two years.")
    if any((right - left).days > 10 for left, right in zip(days, days[1:], strict=False)):
        return unavailable_validation(provider, symbol, "Daily history has a gap over ten days.")
    validation_start = len(bars) * 3 // 5
    reserved_start = len(bars) * 4 // 5
    # The most recent fifth is deliberately excluded from the replay call itself.
    reviewed_bars = bars[:reserved_start]
    rows: list[ValidationRow] = []
    for key, (name, _) in STRATEGIES.items():
        events, _ = replay_events(key, reviewed_bars, budget, risk_budget)
        training = [event for event in events if event.exit_at < bars[validation_start].timestamp]
        validation = [event for event in events if (
            event.signal_at >= bars[validation_start].timestamp
            and event.exit_at < bars[reserved_start].timestamp
        )]
        excluded = len(events) - len(training) - len(validation)
        enough = len(training) >= MIN_TRAIN_TRADES and len(validation) >= MIN_VALIDATION_TRADES
        probability = None
        observed = None
        average_net = None
        brier = None
        if enough:
            train_wins = sum(event.net_pnl > 0 for event in training)
            probability = (train_wins + 2) / (len(training) + 4)
            validation_stats = summarize([
                PerformanceTrade(event.net_pnl, event.initial_risk, event.exit_at)
                for event in validation
            ])
            observed = validation_stats["win_rate"]
            average_net = validation_stats["average_net_pnl"]
            brier = sum(
                (probability - int(event.net_pnl > 0)) ** 2 for event in validation
            ) / len(validation)
        rows.append(ValidationRow(
            strategy=key, name=name, status="reviewable" if enough else "insufficient",
            training_trades=len(training), validation_trades=len(validation),
            boundary_trades_excluded=excluded,
            training_smoothed_win_pct=(
                round(probability * 100, 2) if probability is not None else None
            ),
            validation_win_pct=observed,
            validation_average_net_pnl=average_net,
            validation_brier=round(brier, 4) if brier is not None else None,
        ))
    return ChronologicalValidation(
        status="ready", provider=provider, symbol=symbol,
        rule_version=RULE_VERSION,
        allocation=budget, planned_risk=risk_budget,
        history_from=days[0], history_to=days[-1],
        training_from=days[0], training_to=days[validation_start - 1],
        validation_from=days[validation_start], validation_to=days[reserved_start - 1],
        reserved_from=days[reserved_start], reserved_to=days[-1],
        bars=len(bars), reserved_bars=len(bars) - reserved_start, rows=rows,
    )
