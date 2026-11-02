"""A read-only daily summary of a frozen forward paper period. All figures are simulated."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.core.charges import order_charges
from packages.database.models import (
    AuditEvent,
    PaperAccount,
    PaperDeployment,
    PaperOrder,
    PaperTrade,
)
from packages.paper_trading.costs import summarize_costs
from packages.paper_trading.forward_plan import ForwardPlan
from packages.paper_trading.transactions import reconcile_account
from packages.recorder.freshness import summarize_ages


@dataclass(frozen=True)
class StrategyRow:
    strategy_key: str
    armed: int
    waiting: int
    open_trades: int
    closed_trades: int
    wins: int
    realized_pnl: Decimal
    worst_drawdown: Decimal
    modeled_charges: Decimal = Decimal(0)
    after_charges: Decimal = Decimal(0)
    trades_with_charges: int = 0


@dataclass(frozen=True)
class UnfilledRow:
    strategy_key: str
    status: str
    reason: str
    count: int


@dataclass(frozen=True)
class ForwardReport:
    as_of: date
    start: date
    end: date
    ledger_status: str
    ledger_issues: int
    rows: tuple[StrategyRow, ...]
    freshness: dict[str, Any] = field(default_factory=dict)
    unfilled: tuple[UnfilledRow, ...] = ()

    @property
    def total_realized(self) -> Decimal:
        return sum((row.realized_pnl for row in self.rows), Decimal(0))

    @property
    def total_after_charges(self) -> Decimal:
        return sum((row.after_charges for row in self.rows), Decimal(0))

    @property
    def closed_trades(self) -> int:
        return sum(row.closed_trades for row in self.rows)

    @property
    def trades_with_charges(self) -> int:
        return sum(row.trades_with_charges for row in self.rows)


def drawdown(pnls: list[Decimal]) -> Decimal:
    """Largest fall from a running peak of cumulative P&L, starting from zero."""
    peak = total = worst = Decimal(0)
    for pnl in pnls:
        total += pnl
        peak = max(peak, total)
        worst = max(worst, peak - total)
    return worst


def _unfilled(session: Session, deployments: list[PaperDeployment]) -> tuple[UnfilledRow, ...]:
    """Why armed strategies have no trade, so a cap's silent selection stays visible."""
    waiting = [row for row in deployments if row.trade_id is None]
    blocks: dict[str, str] = {}
    if waiting:
        events = session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.deployment.entry_blocked",
            AuditEvent.correlation_id.in_([str(row.id) for row in waiting]),
        ).order_by(AuditEvent.created_at, AuditEvent.id))
        blocks = {str(event.correlation_id): str(event.details.get("reason")) for event in events}
    counts = Counter(
        (str(row.request_data["strategy_key"]), row.status, blocks.get(str(row.id), row.detail))
        for row in waiting
    )
    return tuple(UnfilledRow(key, status, reason, count)
                 for (key, status, reason), count in sorted(counts.items()))


def _round_trip_charges(trade: PaperTrade, orders: list[PaperOrder]) -> Decimal | None:
    """The backtests' charge schedule on every filled leg, or None if a fill is not on record.

    A trade settled at expiry has no recorded closing fills, so its charges are never estimated.
    """
    summary = summarize_costs(trade.status, trade.realized_pnl, orders)
    if summary["pnl_after_modeled_charges"] is None:
        return None
    total = Decimal(0)
    try:
        for order in orders:
            for fill in order.fill_snapshot["fills"]:
                total += order_charges(
                    instrument="OPT", side=fill["side"],
                    price=Decimal(str(fill["fill_price"])),
                    quantity=int(fill["quantity_units"]), trade_date=order.created_at.date(),
                ).total
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None
    return total


def build_report(
    session: Session, plan: ForwardPlan, account: PaperAccount, as_of: date
) -> ForwardReport:
    """Only deployments of the frozen versions count, so earlier test trades stay out."""
    frozen = {str(item) for item in plan.version_ids}
    deployments = [
        row for row in session.scalars(select(PaperDeployment).where(
            PaperDeployment.user_id == account.user_id,
        ))
        if row.request_data.get("strategy_version_id") in frozen
    ]
    trade_ids = [row.trade_id for row in deployments if row.trade_id is not None]
    trades = {
        row.id: row for row in session.scalars(
            select(PaperTrade).where(PaperTrade.id.in_(trade_ids))
        )
    } if trade_ids else {}
    orders = list(session.scalars(
        select(PaperOrder).where(PaperOrder.trade_id.in_(trade_ids))
    )) if trade_ids else []
    orders_by_trade: dict[Any, list[PaperOrder]] = {}
    for order in orders:
        orders_by_trade.setdefault(order.trade_id, []).append(order)
    keys = sorted({str(row.request_data["strategy_key"]) for row in deployments})
    rows = []
    for key in keys:
        mine = [row for row in deployments if row.request_data["strategy_key"] == key]
        held = [trades[row.trade_id] for row in mine if row.trade_id in trades]
        closed = sorted(
            (row for row in held if row.status == "CLOSED" and row.realized_pnl is not None),
            key=lambda row: (row.closed_at is None, row.closed_at),
        )
        pnls = [row.realized_pnl for row in closed if row.realized_pnl is not None]
        # Only a complete, reconciled round trip counts; missing charges are never estimated.
        costs = [
            (row.realized_pnl - charges, charges) for row, charges in (
                (row, _round_trip_charges(row, orders_by_trade.get(row.id, [])))
                for row in closed
            ) if charges is not None and row.realized_pnl is not None
        ]
        rows.append(StrategyRow(
            strategy_key=key,
            armed=len(mine),
            waiting=sum(row.status == "WAITING_FOR_ENTRY" for row in mine),
            open_trades=sum(row.status == "OPEN" for row in held),
            closed_trades=len(closed),
            wins=sum(pnl > 0 for pnl in pnls),
            realized_pnl=sum(pnls, Decimal(0)),
            worst_drawdown=drawdown(pnls),
            modeled_charges=sum((charges for _, charges in costs), Decimal(0)),
            after_charges=sum((after for after, _ in costs), Decimal(0)),
            trades_with_charges=len(costs),
        ))
    legs = [leg for order in orders for leg in order.fill_snapshot.get("fills", [])]
    ledger = reconcile_account(session, account)
    return ForwardReport(
        as_of=as_of, start=plan.start, end=plan.end, ledger_status=ledger.status,
        ledger_issues=ledger.issue_count,
        rows=tuple(sorted(rows, key=lambda row: row.realized_pnl, reverse=True)),
        freshness=summarize_ages(legs),
        unfilled=_unfilled(session, deployments),
    )


def _after_charges(row: StrategyRow) -> str:
    if row.trades_with_charges < row.closed_trades:
        return f"{row.trades_with_charges} of {row.closed_trades} trades only"
    return f"{row.after_charges:+,.2f}"


def render_markdown(report: ForwardReport) -> str:
    lines = [
        f"# Forward paper report, {report.as_of.isoformat()}",
        "",
        f"Simulated, not advice. Period {report.start.isoformat()} to {report.end.isoformat()}.",
        f"Ledger: {report.ledger_status} ({report.ledger_issues} issues).",
        f"Realized P&L of the frozen strategies, before charges: {report.total_realized:+,.2f}.",
        f"After estimated charges: {report.total_after_charges:+,.2f}, covering "
        f"{report.trades_with_charges} of {report.closed_trades} closed trades.",
        "Charges use the backtests' schedule (brokerage, STT, exchange, SEBI, stamp duty, GST) on "
        "each filled leg. They are not reviewed against an account statement, and they are not "
        "the illustrative charges shown on Trade.",
        "",
        "| Strategy | Armed | Waiting | Open | Closed | Won | Realized | Charges | After charges "
        "| Worst drawdown |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {row.strategy_key.replace('_', ' ')} | {row.armed} | {row.waiting} | "
        f"{row.open_trades} | {row.closed_trades} | {row.wins} | {row.realized_pnl:+,.2f} | "
        f"{row.modeled_charges:,.2f} | {_after_charges(row)} | {row.worst_drawdown:,.2f} |"
        for row in report.rows
    ]
    if report.unfilled:
        lines += [
            "",
            "Armed with no trade. These are missing from the results above, so the strategies",
            "that did trade are not a full sample of the frozen set.",
            "",
            "| Strategy | Status | Times | Last block or check |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {row.strategy_key.replace('_', ' ')} | {row.status} | {row.count} | {row.reason} |"
            for row in report.unfilled
        ]
    fresh = report.freshness
    if fresh:
        lines += [
            "",
            f"Last-trade age at fill, from the recorded feed: {fresh['with_age']} of "
            f"{fresh['legs']} legs have one; median {fresh['median_seconds']} s, "
            f"90th percentile {fresh['p90_seconds']} s, {fresh['over_60_seconds']} over 60 s. "
            "This dates the last trade, not the bid/ask.",
        ]
    return "\n".join(lines) + "\n"
