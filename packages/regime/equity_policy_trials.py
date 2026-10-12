"""Predeclared equity exit-policy comparisons on a frozen chronological dataset."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from packages.backtesting.performance import PerformanceTrade, summarize
from packages.market_data.contracts import HistoricalBar
from packages.paper_trading.equity import (
    DEFAULT_EXIT_POLICY,
    STRATEGIES,
    ZONE,
    EquityExitPolicy,
    replay_events,
)

REGISTRY_VERSION = "equity-exit-trials-v1"
MIN_TRADES_PER_WINDOW = 20
POLICIES = (
    DEFAULT_EXIT_POLICY,
    EquityExitPolicy(
        "equity-exit-target-1p5-research-v1", Decimal("2"), Decimal("0.005"),
        Decimal("1.5"), Decimal("1"), Decimal("1"),
    ),
    EquityExitPolicy(
        "equity-exit-target-3-research-v1", Decimal("2"), Decimal("0.005"),
        Decimal("3"), Decimal("1"), Decimal("1"),
    ),
)


class PolicyTrialRow(BaseModel):
    policy_version: str
    target_r: Decimal
    trail_activation_r: Decimal
    trail_distance_r: Decimal
    status: Literal["insufficient", "reviewable"]
    training_trades: int
    validation_trades: int
    boundary_trades_excluded: int
    training_win_pct: float | None = None
    validation_win_pct: float | None = None
    training_average_r: float | None = None
    validation_average_r: float | None = None
    validation_average_net_pnl: float | None = None


class PolicyComparison(BaseModel):
    status: Literal["ready", "unavailable"]
    reason: str | None = None
    provider: str
    symbol: str
    strategy: str
    study_id: str | None = None
    trial_id: UUID | None = None
    frozen_at: datetime | None = None
    comparison_digest: str | None = None
    dataset_digest: str | None = None
    registry_version: str = REGISTRY_VERSION
    training_to: date | None = None
    validation_to: date | None = None
    reserved_from: date | None = None
    rows: list[PolicyTrialRow] = Field(default_factory=list)
    holdout_evaluated: bool = False
    ranking_changed: bool = False
    parameters_changed: bool = False
    method: str = (
        "Three policies were declared before this comparison. Each replay uses only the "
        "first 80% of the frozen daily bars; the newest 20% is untouched. Trades crossing "
        "the training/validation boundary are excluded. Rates are withheld until each "
        "policy has at least 20 closed trades in both earlier windows. This is research "
        "only: corporate-action and point-in-time provenance, an acceptance decision, "
        "the reserved holdout and live-paper reconciliation are still required."
    )


def compare_exit_policies(
    bars: list[HistoricalBar], *, provider: str, symbol: str, strategy: str,
    study_id: str, dataset_digest: str, budget: Decimal, risk_budget: Decimal,
) -> PolicyComparison:
    if strategy not in STRATEGIES:
        raise ValueError("Unknown paper strategy")
    if len(bars) < 400:
        raise ValueError("Frozen study has too few completed daily bars")
    training_start = len(bars) * 3 // 5
    reserved_start = len(bars) * 4 // 5
    reviewed = bars[:reserved_start]
    training_boundary = bars[training_start].timestamp
    reserved_boundary = bars[reserved_start].timestamp
    rows = []
    for policy in POLICIES:
        trades, _ = replay_events(
            strategy, reviewed, budget, risk_budget, policy=policy
        )
        training = [trade for trade in trades if trade.exit_at < training_boundary]
        validation = [trade for trade in trades if (
            trade.signal_at >= training_boundary and trade.exit_at < reserved_boundary
        )]
        enough = (len(training) >= MIN_TRADES_PER_WINDOW
                  and len(validation) >= MIN_TRADES_PER_WINDOW)
        train_stats = summarize([
            PerformanceTrade(trade.net_pnl, trade.initial_risk, trade.exit_at)
            for trade in training
        ]) if enough else None
        valid_stats = summarize([
            PerformanceTrade(trade.net_pnl, trade.initial_risk, trade.exit_at)
            for trade in validation
        ]) if enough else None
        rows.append(PolicyTrialRow(
            policy_version=policy.version,
            target_r=policy.target_r,
            trail_activation_r=policy.trail_activation_r,
            trail_distance_r=policy.trail_distance_r,
            status="reviewable" if enough else "insufficient",
            training_trades=len(training), validation_trades=len(validation),
            boundary_trades_excluded=len(trades) - len(training) - len(validation),
            training_win_pct=train_stats["win_rate"] if train_stats else None,
            validation_win_pct=valid_stats["win_rate"] if valid_stats else None,
            training_average_r=train_stats["average_r"] if train_stats else None,
            validation_average_r=valid_stats["average_r"] if valid_stats else None,
            validation_average_net_pnl=(
                valid_stats["average_net_pnl"] if valid_stats else None
            ),
        ))
    return PolicyComparison(
        status="ready", provider=provider, symbol=symbol, strategy=strategy,
        study_id=study_id, dataset_digest=dataset_digest,
        training_to=bars[training_start - 1].timestamp.astimezone(ZONE).date(),
        validation_to=bars[reserved_start - 1].timestamp.astimezone(ZONE).date(),
        reserved_from=reserved_boundary.astimezone(ZONE).date(), rows=rows,
    )
