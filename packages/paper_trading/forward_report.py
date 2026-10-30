"""A read-only daily summary of a frozen forward paper period. All figures are simulated."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.database.models import PaperAccount, PaperDeployment, PaperTrade
from packages.paper_trading.forward_plan import ForwardPlan
from packages.paper_trading.transactions import reconcile_account


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


@dataclass(frozen=True)
class ForwardReport:
    as_of: date
    start: date
    end: date
    ledger_status: str
    ledger_issues: int
    rows: tuple[StrategyRow, ...]

    @property
    def total_realized(self) -> Decimal:
        return sum((row.realized_pnl for row in self.rows), Decimal(0))


def drawdown(pnls: list[Decimal]) -> Decimal:
    """Largest fall from a running peak of cumulative P&L, starting from zero."""
    peak = total = worst = Decimal(0)
    for pnl in pnls:
        total += pnl
        peak = max(peak, total)
        worst = max(worst, peak - total)
    return worst


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
        rows.append(StrategyRow(
            strategy_key=key,
            armed=len(mine),
            waiting=sum(row.status == "WAITING_FOR_ENTRY" for row in mine),
            open_trades=sum(row.status == "OPEN" for row in held),
            closed_trades=len(closed),
            wins=sum(pnl > 0 for pnl in pnls),
            realized_pnl=sum(pnls, Decimal(0)),
            worst_drawdown=drawdown(pnls),
        ))
    ledger = reconcile_account(session, account)
    return ForwardReport(
        as_of=as_of, start=plan.start, end=plan.end, ledger_status=ledger.status,
        ledger_issues=ledger.issue_count,
        rows=tuple(sorted(rows, key=lambda row: row.realized_pnl, reverse=True)),
    )


def render_markdown(report: ForwardReport) -> str:
    lines = [
        f"# Forward paper report, {report.as_of.isoformat()}",
        "",
        f"Simulated, not advice. Period {report.start.isoformat()} to {report.end.isoformat()}.",
        f"Ledger: {report.ledger_status} ({report.ledger_issues} issues).",
        f"Realized P&L of the frozen strategies: {report.total_realized:+,.2f}.",
        "",
        "| Strategy | Armed | Waiting | Open | Closed | Won | Realized | Worst drawdown |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {row.strategy_key.replace('_', ' ')} | {row.armed} | {row.waiting} | "
        f"{row.open_trades} | {row.closed_trades} | {row.wins} | {row.realized_pnl:+,.2f} | "
        f"{row.worst_drawdown:,.2f} |"
        for row in report.rows
    ]
    return "\n".join(lines) + "\n"
