"""Automatic demotion: pause new paper entries for a strategy whose results break limits.

A strategy is paused after a run of consecutive losing closed trades, or when the
peak-to-trough drawdown of its realized P&L per lot exceeds a multiple of its backtest
maximum drawdown per lot. Without a usable backtest baseline the drawdown limit falls
back to a fixed share of starting capital per lot. Only trades closed since the last
reinstatement count, and labelled expiry tests are left out. A pause blocks new entries
only; open positions keep their exits. Reinstating is an explicit, audited owner action.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.database.futures_models import PaperFuturesTrade
from packages.database.models import (
    AuditEvent,
    PaperTrade,
    StrategyDemotion,
    StrategyDrawdownBaseline,
)

DEMOTION_POLICY_ID = "paper-strategy-demotion-v1"
MIN_BASELINE_TRADES = 10
_CENT = Decimal("0.01")
DemotionReason = Literal["LOSS_STREAK", "DRAWDOWN"]


@dataclass(frozen=True)
class DemotionLimits:
    loss_streak: int
    drawdown_multiple: Decimal
    fallback_drawdown_per_lot: Decimal

    def __post_init__(self) -> None:
        if (
            self.loss_streak < 2
            or self.drawdown_multiple < 1
            or self.fallback_drawdown_per_lot <= 0
        ):
            raise ValueError("Demotion limits must be a streak of at least 2, a multiple of "
                             "at least 1 and a positive fallback drawdown")


@dataclass(frozen=True)
class DemotionCheck:
    reason: DemotionReason | None
    trades_counted: int
    longest_loss_streak: int
    max_drawdown_per_lot: Decimal
    drawdown_limit_per_lot: Decimal
    limit_source: Literal["BACKTEST", "FALLBACK"]
    limits: DemotionLimits

    def snapshot(self) -> dict[str, Any]:
        return {
            "policy_id": DEMOTION_POLICY_ID,
            "reason": self.reason,
            "trades_counted": self.trades_counted,
            "longest_loss_streak": self.longest_loss_streak,
            "loss_streak_limit": self.limits.loss_streak,
            "max_drawdown_per_lot": str(self.max_drawdown_per_lot),
            "drawdown_limit_per_lot": str(self.drawdown_limit_per_lot),
            "limit_source": self.limit_source,
            "drawdown_multiple": str(self.limits.drawdown_multiple),
        }

    def describe(self) -> str:
        if self.reason == "LOSS_STREAK":
            return f"{self.longest_loss_streak} consecutive losing paper trades"
        return (
            f"a drawdown of ₹{self.max_drawdown_per_lot} per lot, above the "
            f"₹{self.drawdown_limit_per_lot} limit"
        )


def max_drawdown(pnl: Iterable[Decimal]) -> Decimal:
    """Largest peak-to-trough fall of cumulative P&L, starting from a peak of zero.

    Paper demotion and backtest baselines both use this, so the two stay comparable.
    """
    cumulative = peak = worst = Decimal(0)
    for value in pnl:
        cumulative += value
        peak = max(peak, cumulative)
        worst = max(worst, peak - cumulative)
    return worst


def evaluate_demotion(
    pnl_per_lot: Sequence[Decimal],
    limits: DemotionLimits,
    baseline_drawdown_per_lot: Decimal | None,
) -> DemotionCheck:
    """Check realized P&L per lot, in closing order, against both demotion rules.

    Uses the longest streak and the largest drawdown in the window, not just the latest
    values, so several trades closing between two entry checks cannot hide a breach.
    """
    longest = current = 0
    for pnl in pnl_per_lot:
        current = current + 1 if pnl < 0 else 0
        longest = max(longest, current)
    drawdown = max_drawdown(pnl_per_lot)
    if baseline_drawdown_per_lot is not None and baseline_drawdown_per_lot > 0:
        limit = baseline_drawdown_per_lot * limits.drawdown_multiple
        source: Literal["BACKTEST", "FALLBACK"] = "BACKTEST"
    else:
        limit, source = limits.fallback_drawdown_per_lot, "FALLBACK"
    reason: DemotionReason | None = None
    if longest >= limits.loss_streak:
        reason = "LOSS_STREAK"
    elif drawdown > limit:
        reason = "DRAWDOWN"
    return DemotionCheck(
        reason=reason,
        trades_counted=len(pnl_per_lot),
        longest_loss_streak=longest,
        max_drawdown_per_lot=drawdown.quantize(_CENT, rounding=ROUND_HALF_UP),
        drawdown_limit_per_lot=limit.quantize(_CENT, rounding=ROUND_HALF_UP),
        limit_source=source,
        limits=limits,
    )


def active_demotion(
    session: Session, user_id: UUID, strategy_key: str
) -> StrategyDemotion | None:
    return session.scalar(select(StrategyDemotion).where(
        StrategyDemotion.user_id == user_id,
        StrategyDemotion.strategy_key == strategy_key,
        StrategyDemotion.status == "ACTIVE",
    ).order_by(StrategyDemotion.demoted_at.desc(), StrategyDemotion.id.desc()))


def drawdown_baseline(session: Session, strategy_key: str) -> StrategyDrawdownBaseline | None:
    """Latest backtest baseline with enough trades to be meaningful."""
    return session.scalar(select(StrategyDrawdownBaseline).where(
        StrategyDrawdownBaseline.strategy_key == strategy_key,
        StrategyDrawdownBaseline.trades >= MIN_BASELINE_TRADES,
    ).order_by(StrategyDrawdownBaseline.created_at.desc(), StrategyDrawdownBaseline.id.desc()))


def record_drawdown_baseline(
    session: Session,
    strategy_key: str,
    pnl_per_lot: Sequence[Decimal],
    *,
    source: str,
    details: dict[str, Any],
) -> StrategyDrawdownBaseline:
    """Stage a backtest drawdown baseline; refuses samples too small to be used."""
    if len(pnl_per_lot) < MIN_BASELINE_TRADES:
        raise ValueError(
            f"A drawdown baseline needs at least {MIN_BASELINE_TRADES} backtest trades; "
            f"got {len(pnl_per_lot)}"
        )
    baseline = StrategyDrawdownBaseline(
        strategy_key=strategy_key,
        max_drawdown_per_lot=max_drawdown(pnl_per_lot).quantize(_CENT, rounding=ROUND_HALF_UP),
        trades=len(pnl_per_lot),
        source=source,
        details=details,
        created_at=datetime.now(timezone.utc),
    )
    session.add(baseline)
    return baseline


def check_strategy(
    session: Session, user_id: UUID, strategy_key: str, limits: DemotionLimits
) -> DemotionCheck:
    since = session.scalar(select(StrategyDemotion.reinstated_at).where(
        StrategyDemotion.user_id == user_id,
        StrategyDemotion.strategy_key == strategy_key,
        StrategyDemotion.status == "REINSTATED",
    ).order_by(StrategyDemotion.reinstated_at.desc()))
    query = select(PaperTrade).where(
        PaperTrade.user_id == user_id,
        PaperTrade.strategy_key == strategy_key,
        PaperTrade.status == "CLOSED",
        PaperTrade.realized_pnl.is_not(None),
        PaperTrade.closed_at.is_not(None),
    )
    if since is not None:
        query = query.where(PaperTrade.closed_at > since)
    trades = [
        trade for trade in session.scalars(query.order_by(PaperTrade.closed_at, PaperTrade.id))
        if trade.entry_rule_snapshot.get("testing_only") is not True
    ]
    futures_query = select(PaperFuturesTrade).where(
        PaperFuturesTrade.user_id == user_id,
        PaperFuturesTrade.strategy_key == strategy_key,
        PaperFuturesTrade.status == "CLOSED",
        PaperFuturesTrade.realized_pnl.is_not(None),
        PaperFuturesTrade.closed_at.is_not(None),
    )
    if since is not None:
        futures_query = futures_query.where(PaperFuturesTrade.closed_at > since)
    closed = sorted(
        [*trades, *session.scalars(futures_query)],
        key=lambda trade: (trade.closed_at, str(trade.id)),
    )
    baseline = drawdown_baseline(session, strategy_key)
    return evaluate_demotion(
        [Decimal(trade.realized_pnl or 0) / trade.lots for trade in closed],
        limits,
        baseline.max_drawdown_per_lot if baseline is not None else None,
    )


def enforce_strategy_demotion(
    session: Session, user_id: UUID, strategy_key: str, limits: DemotionLimits
) -> StrategyDemotion | None:
    """Return the pause blocking new entries, staging a new one when a limit is breached.

    Call while holding the account lock; the caller commits a staged pause before
    rejecting the entry so it survives the rejection.
    """
    existing = active_demotion(session, user_id, strategy_key)
    if existing is not None:
        return existing
    check = check_strategy(session, user_id, strategy_key, limits)
    if check.reason is None:
        return None
    now = datetime.now(timezone.utc)
    demotion = StrategyDemotion(
        user_id=user_id, strategy_key=strategy_key, status="ACTIVE", reason=check.reason,
        details={**check.snapshot(), "summary": check.describe()}, demoted_at=now,
    )
    session.add(demotion)
    session.add(AuditEvent(
        actor_user_id=user_id,
        event_type="paper.strategy.demoted",
        details={"strategy_key": strategy_key, **check.snapshot()},
    ))
    return demotion


def reinstate_strategy(
    session: Session, user_id: UUID, strategy_key: str
) -> StrategyDemotion | None:
    demotion = active_demotion(session, user_id, strategy_key)
    if demotion is None:
        return None
    demotion.status = "REINSTATED"
    demotion.reinstated_at = datetime.now(timezone.utc)
    session.add(AuditEvent(
        actor_user_id=user_id,
        event_type="paper.strategy.reinstated",
        details={"strategy_key": strategy_key, "demotion_id": str(demotion.id),
                 "reason": demotion.reason},
    ))
    return demotion
