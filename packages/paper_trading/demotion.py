"""Automatic demotion: pause new paper entries for a strategy whose results break limits.

A strategy is paused after a run of consecutive losing closed trades, or when the
peak-to-trough drawdown of its realized P&L per lot exceeds a multiple of its backtest
maximum drawdown per lot. Without a usable backtest baseline the drawdown limit falls
back to a fixed share of starting capital per lot. Only trades closed since the last
reinstatement count, and labelled expiry tests are left out. A pause blocks new entries
only; open positions keep their exits. A closed trade with no usable P&L is missing
evidence and also pauses the strategy.

The way back is rule-based and needs no model: after a cooling-off period the strategy goes
on probation (one lot, one position or armed entry at a time, never picked by the ranking).
It is promoted once enough probation trades close with a positive total, inside tighter
limits, and against a saved backtest baseline. A breach restarts the cooling-off period.
Every step is appended to strategy_eligibility_events. These rules only gate eligibility:
they never change a strategy's own rules or the account risk limits. The owner can still
reinstate a strategy after review.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from packages.database.clock import monotonic_utc_now
from packages.database.futures_models import PaperFuturesTrade
from packages.database.models import (
    AuditEvent,
    PaperTrade,
    StrategyDemotion,
    StrategyDrawdownBaseline,
    StrategyEligibilityEvent,
    UserNotification,
)

DEMOTION_POLICY_ID = "paper-strategy-demotion-v2"
MIN_BASELINE_TRADES = 10
PROBATION_MAX_LOTS = 1
_CENT = Decimal("0.01")
DemotionReason = Literal["LOSS_STREAK", "DRAWDOWN", "EVIDENCE_MISSING"]
EligibilityStage = Literal["ELIGIBLE", "DEMOTED", "PROBATION"]
EligibilityEventType = Literal[
    "DEMOTED", "PROBATION_STARTED", "PROBATION_FAILED", "PROMOTED", "REINSTATED"
]


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
class PromotionPolicy:
    cooldown_days: int
    min_trades: int
    loss_streak: int

    def __post_init__(self) -> None:
        if self.cooldown_days < 1 or self.min_trades < 3 or self.loss_streak < 2:
            raise ValueError("Promotion needs at least 1 cooling-off day, 3 probation trades "
                             "and a loss streak limit of at least 2")


@dataclass(frozen=True)
class DemotionCheck:
    reason: DemotionReason | None
    trades_counted: int
    longest_loss_streak: int
    max_drawdown_per_lot: Decimal
    drawdown_limit_per_lot: Decimal
    limit_source: Literal["BACKTEST", "FALLBACK"]
    limits: DemotionLimits
    net_pnl_per_lot: Decimal = Decimal(0)
    trades_missing_evidence: int = 0

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
            "net_pnl_per_lot": str(self.net_pnl_per_lot),
            "trades_missing_evidence": self.trades_missing_evidence,
        }

    def describe(self) -> str:
        if self.reason == "EVIDENCE_MISSING":
            return f"{self.trades_missing_evidence} closed paper trade(s) with no usable P&L"
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
    *,
    trades_missing_evidence: int = 0,
) -> DemotionCheck:
    """Check realized P&L per lot, in closing order, against both demotion rules.

    Uses the longest streak and the largest drawdown in the window, not just the latest
    values, so several trades closing between two entry checks cannot hide a breach.
    Closed trades without usable P&L fail closed: the window can no longer be trusted.
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
    if trades_missing_evidence > 0:
        reason = "EVIDENCE_MISSING"
    elif longest >= limits.loss_streak:
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
        net_pnl_per_lot=sum(pnl_per_lot, Decimal(0)).quantize(_CENT, rounding=ROUND_HALF_UP),
        trades_missing_evidence=trades_missing_evidence,
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


def _closed_trades(
    session: Session, user_id: UUID, strategy_key: str, since: datetime | None
) -> tuple[list[PaperTrade | PaperFuturesTrade], int]:
    """Closed non-test option and futures trades in the window, and how many lack usable P&L.

    A trade with no close time cannot be placed outside any window, so it always counts.
    """
    query = select(PaperTrade).where(
        PaperTrade.user_id == user_id,
        PaperTrade.strategy_key == strategy_key,
        PaperTrade.status == "CLOSED",
    )
    if since is not None:
        query = query.where(or_(PaperTrade.closed_at > since, PaperTrade.closed_at.is_(None)))
    usable: list[PaperTrade | PaperFuturesTrade] = []
    missing = 0
    for trade in session.scalars(query):
        if trade.entry_rule_snapshot.get("testing_only") is True:
            continue
        if trade.realized_pnl is None or trade.closed_at is None:
            missing += 1
        else:
            usable.append(trade)
    futures_query = select(PaperFuturesTrade).where(
        PaperFuturesTrade.user_id == user_id,
        PaperFuturesTrade.strategy_key == strategy_key,
        PaperFuturesTrade.status == "CLOSED",
    )
    if since is not None:
        futures_query = futures_query.where(
            or_(PaperFuturesTrade.closed_at > since, PaperFuturesTrade.closed_at.is_(None))
        )
    for future in session.scalars(futures_query):
        if future.realized_pnl is None or future.closed_at is None:
            missing += 1
        else:
            usable.append(future)
    usable.sort(key=lambda trade: (as_utc(cast(datetime, trade.closed_at)), str(trade.id)))
    return usable, missing


def _check_window(
    session: Session, user_id: UUID, strategy_key: str, limits: DemotionLimits,
    since: datetime | None,
) -> tuple[DemotionCheck, list[PaperTrade | PaperFuturesTrade]]:
    trades, missing = _closed_trades(session, user_id, strategy_key, since)
    baseline = drawdown_baseline(session, strategy_key)
    check = evaluate_demotion(
        [Decimal(trade.realized_pnl or 0) / trade.lots for trade in trades],
        limits,
        baseline.max_drawdown_per_lot if baseline is not None else None,
        trades_missing_evidence=missing,
    )
    return check, trades


def check_strategy(
    session: Session, user_id: UUID, strategy_key: str, limits: DemotionLimits
) -> DemotionCheck:
    since = session.scalar(select(StrategyDemotion.reinstated_at).where(
        StrategyDemotion.user_id == user_id,
        StrategyDemotion.strategy_key == strategy_key,
        StrategyDemotion.status == "REINSTATED",
    ).order_by(StrategyDemotion.reinstated_at.desc()))
    return _check_window(session, user_id, strategy_key, limits, since)[0]


@dataclass(frozen=True)
class StrategyEligibility:
    """Where one strategy stands, including a step that is due but not yet recorded."""

    strategy_key: str
    stage: EligibilityStage
    check: DemotionCheck
    demotion: StrategyDemotion | None = None
    reason: str | None = None
    summary: str | None = None
    probation_starts_at: datetime | None = None
    probation_trades: int = 0
    probation_trades_needed: int = 0
    promotion_needs: tuple[str, ...] = ()
    due: EligibilityEventType | None = None

    @property
    def block_reason(self) -> str | None:
        """Why a new paper entry or the ranking must not treat this strategy as eligible."""
        if self.stage == "DEMOTED":
            waiting = (
                f" Probation can start on {self.probation_starts_at.date().isoformat()}."
                if self.probation_starts_at is not None else ""
            )
            return f"Paused after {self.summary or self.reason}.{waiting}"
        if self.stage == "PROBATION":
            return (
                f"On probation after {self.summary or self.reason}: "
                f"{self.probation_trades} of {self.probation_trades_needed} trades, "
                f"{PROBATION_MAX_LOTS} lot at a time, never selected by the ranking."
            )
        return None


def as_utc(moment: datetime) -> datetime:
    # SQLite stores these times without an offset; they are always UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _latest_probation_event(
    session: Session, demotion: StrategyDemotion
) -> StrategyEligibilityEvent | None:
    return session.scalar(select(StrategyEligibilityEvent).where(
        StrategyEligibilityEvent.demotion_id == demotion.id,
        StrategyEligibilityEvent.event_type.in_(("PROBATION_STARTED", "PROBATION_FAILED")),
    ).order_by(
        StrategyEligibilityEvent.occurred_at.desc(), StrategyEligibilityEvent.id.desc()
    ).limit(1))


def assess_strategy(
    session: Session, user_id: UUID, strategy_key: str, limits: DemotionLimits,
    policy: PromotionPolicy, now: datetime | None = None,
) -> StrategyEligibility:
    """Work out a strategy's stage without writing anything; ranking and status use this."""
    now = now or datetime.now(timezone.utc)
    demotion = active_demotion(session, user_id, strategy_key)
    if demotion is None:
        check = check_strategy(session, user_id, strategy_key, limits)
        if check.reason is None:
            return StrategyEligibility(strategy_key, "ELIGIBLE", check)
        return StrategyEligibility(
            strategy_key, "DEMOTED", check, reason=check.reason, summary=check.describe(),
            probation_starts_at=now + timedelta(days=policy.cooldown_days), due="DEMOTED",
        )
    summary = str(demotion.details.get("summary") or demotion.reason)
    latest = _latest_probation_event(session, demotion)
    if latest is None or latest.event_type == "PROBATION_FAILED":
        cooling_from = as_utc(latest.occurred_at if latest else demotion.demoted_at)
        ready_at = cooling_from + timedelta(days=policy.cooldown_days)
        check = check_strategy(session, user_id, strategy_key, limits)
        if now < ready_at:
            return StrategyEligibility(
                strategy_key, "DEMOTED", check, demotion,
                latest.reason if latest else demotion.reason,
                latest.summary if latest else summary, probation_starts_at=ready_at,
                probation_trades_needed=policy.min_trades,
            )
        return StrategyEligibility(
            strategy_key, "PROBATION", check, demotion, demotion.reason, summary,
            probation_trades_needed=policy.min_trades,
            promotion_needs=(f"{policy.min_trades} more closed probation trade(s)",),
            due="PROBATION_STARTED",
        )
    since = as_utc(latest.occurred_at)
    tighter = replace(
        limits, loss_streak=min(limits.loss_streak, policy.loss_streak),
        drawdown_multiple=Decimal(1),
    )
    check, trades = _check_window(session, user_id, strategy_key, tighter, since)
    # Positions opened before probation can fail it but cannot earn promotion.
    counted = sum(as_utc(trade.opened_at) >= since for trade in trades)
    if check.reason is not None:
        return StrategyEligibility(
            strategy_key, "DEMOTED", check, demotion, check.reason, check.describe(),
            probation_starts_at=now + timedelta(days=policy.cooldown_days),
            probation_trades=counted, probation_trades_needed=policy.min_trades,
            due="PROBATION_FAILED",
        )
    needs: list[str] = []
    if counted < policy.min_trades:
        needs.append(f"{policy.min_trades - counted} more closed probation trade(s)")
    if check.net_pnl_per_lot <= 0:
        needs.append("a positive total P&L per lot across probation trades")
    if check.limit_source != "BACKTEST":
        needs.append("a saved backtest drawdown baseline")
    return StrategyEligibility(
        strategy_key, "PROBATION" if needs else "ELIGIBLE", check, demotion, demotion.reason,
        summary, probation_trades=counted, probation_trades_needed=policy.min_trades,
        promotion_needs=tuple(needs), due=None if needs else "PROMOTED",
    )


_EVENT_TEXT: dict[str, tuple[str, str]] = {
    "DEMOTED": ("Strategy paused", "New paper entries for {key} are paused after {summary}."),
    "PROBATION_STARTED": (
        "Strategy on probation",
        "Cooling-off ended for {key}: one-lot paper entries are allowed while it is on probation.",
    ),
    "PROBATION_FAILED": (
        "Strategy probation failed",
        "Probation for {key} ended after {summary}; the cooling-off period restarts.",
    ),
    "PROMOTED": (
        "Strategy eligible again",
        "{key} passed probation ({summary}); normal paper entries are allowed.",
    ),
}


def _record_event(
    session: Session, user_id: UUID, strategy_key: str, demotion: StrategyDemotion,
    event_type: EligibilityEventType, reason: str, summary: str, details: dict[str, Any],
    occurred_at: datetime,
) -> None:
    session.add(StrategyEligibilityEvent(
        user_id=user_id, strategy_key=strategy_key, demotion_id=demotion.id,
        event_type=event_type, reason=reason, summary=summary[:256], details=details,
        occurred_at=occurred_at,
    ))
    session.add(AuditEvent(
        actor_user_id=user_id,
        event_type=f"paper.strategy.{event_type.lower()}",
        details={**details, "strategy_key": strategy_key, "demotion_id": str(demotion.id),
                 "reason": reason},
    ))


def apply_strategy_eligibility(
    session: Session, user_id: UUID, strategy_key: str, limits: DemotionLimits,
    policy: PromotionPolicy, now: datetime | None = None,
) -> StrategyEligibility:
    """Assess a strategy and stage any step that is due, one step per call.

    Call while holding the account lock. The caller commits; a refused entry commits first
    so a new pause survives the rejection.
    """
    now = now or monotonic_utc_now()
    assessed = assess_strategy(session, user_id, strategy_key, limits, policy, now)
    due = assessed.due
    if due is None:
        return assessed
    demotion = assessed.demotion
    snapshot = assessed.check.snapshot()
    if due == "DEMOTED":
        demotion = StrategyDemotion(
            user_id=user_id, strategy_key=strategy_key, status="ACTIVE",
            reason=assessed.reason, details={**snapshot, "summary": assessed.summary},
            demoted_at=now,
        )
        session.add(demotion)
        session.flush()
    if demotion is None:
        raise RuntimeError("Only a new demotion can be staged without an active demotion")
    if due == "PROBATION_STARTED":
        reason, summary = "COOLDOWN_ELAPSED", f"{policy.cooldown_days} day cooling-off ended"
    elif due == "PROMOTED":
        reason = "PROBATION_PASSED"
        summary = (
            f"{assessed.probation_trades} probation trades, "
            f"₹{assessed.check.net_pnl_per_lot} per lot in total"
        )
        demotion.status = "REINSTATED"
        demotion.reinstated_at = now
    else:
        reason, summary = str(assessed.reason), str(assessed.summary)
    _record_event(session, user_id, strategy_key, demotion, due, reason, summary, snapshot, now)
    title, message = _EVENT_TEXT[due]
    session.add(UserNotification(
        user_id=user_id, event_type=f"paper.strategy.{due.lower()}", title=title,
        message=message.format(key=strategy_key, summary=summary),
        details={"strategy_key": strategy_key, "reason": reason},
    ))
    return replace(assessed, demotion=demotion, due=None)


def reinstate_strategy(
    session: Session, user_id: UUID, strategy_key: str
) -> StrategyDemotion | None:
    demotion = active_demotion(session, user_id, strategy_key)
    if demotion is None:
        return None
    now = monotonic_utc_now()
    demotion.status = "REINSTATED"
    demotion.reinstated_at = now
    _record_event(
        session, user_id, strategy_key, demotion, "REINSTATED", "OWNER_REVIEW",
        "Reinstated by the owner after review", {"demotion_reason": demotion.reason}, now,
    )
    return demotion


def eligibility_history(
    session: Session, user_id: UUID, limit: int = 100
) -> list[StrategyEligibilityEvent]:
    return list(session.scalars(select(StrategyEligibilityEvent).where(
        StrategyEligibilityEvent.user_id == user_id,
    ).order_by(
        StrategyEligibilityEvent.occurred_at.desc(), StrategyEligibilityEvent.id.desc()
    ).limit(limit)))
