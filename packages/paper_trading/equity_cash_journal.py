"""Atomic paper-wallet cash events and read-only journal reconciliation."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.database.models import EquityAccount, EquityCashEvent, EquityDeployment
from packages.paper_trading.equity import money


@dataclass(frozen=True)
class EquityCashJournalReport:
    status: Literal["MATCHED", "LEGACY_CHECKPOINT", "NOT_STARTED", "MISMATCH"]
    entry_count: int
    checkpoint_cash: Decimal | None
    recorded_cash: Decimal
    journal_cash: Decimal | None
    issue_count: int
    issues: list[str]


class EquityCashJournalMismatch(RuntimeError):
    """Prevent new paper-wallet mutations while saved cash evidence disagrees."""


def ensure_equity_cash_journal(session: Session, account: EquityAccount, *, created: bool) -> None:
    """Start a journal under the caller's wallet lock, without rewriting legacy history."""
    opening = session.scalar(select(EquityCashEvent.id).where(
        EquityCashEvent.user_id == account.user_id,
        EquityCashEvent.event_key == "opening",
    ))
    if opening is not None:
        return
    session.add(EquityCashEvent(
        user_id=account.user_id, event_key="opening", deployment_id=None,
        kind="INITIAL_FUNDING" if created else "LEGACY_CHECKPOINT",
        delta=account.initial_cash if created else Decimal(0),
        cash_after=account.cash,
    ))
    session.flush()


def record_equity_cash_event(
    session: Session, account: EquityAccount, deployment_id: UUID,
    *, kind: Literal["RESERVE", "RELEASE", "CLOSE"], delta: Decimal,
) -> None:
    """Call in the same transaction immediately after changing account.cash."""
    session.add(EquityCashEvent(
        user_id=account.user_id, deployment_id=deployment_id,
        event_key=f"{deployment_id}:{kind}", kind=kind,
        delta=money(delta), cash_after=money(account.cash),
    ))


def reconcile_equity_cash_journal(
    *, user_id: UUID, initial_cash: Decimal, recorded_cash: Decimal,
    events: list[EquityCashEvent], deployments: list[EquityDeployment],
) -> EquityCashJournalReport:
    issues: list[str] = []
    issue_count = 0

    def flag(message: str) -> None:
        nonlocal issue_count
        issue_count += 1
        if len(issues) < 20:
            issues.append(message)

    if not events:
        return EquityCashJournalReport(
            status="NOT_STARTED", entry_count=0, checkpoint_cash=None,
            recorded_cash=recorded_cash, journal_cash=None, issue_count=0, issues=[],
        )
    first = events[0]
    if first.user_id != user_id or first.event_key != "opening":
        flag("Journal does not begin with this account's opening record.")
    if first.kind == "INITIAL_FUNDING":
        if first.delta != initial_cash or first.cash_after != initial_cash:
            flag("Initial funding differs from the saved starting capital.")
    elif first.kind == "LEGACY_CHECKPOINT":
        if first.delta != 0:
            flag("Legacy checkpoint must not claim a cash movement.")
    else:
        flag("Journal opening type is invalid.")
    balance = money(first.cash_after)
    by_id = {row.id: row for row in deployments if row.user_id == user_id}
    for event in events[1:]:
        if event.user_id != user_id:
            flag("Journal contains another account's cash entry.")
            continue
        if event.kind not in {"RESERVE", "RELEASE", "CLOSE"}:
            flag(f"Journal entry {event.id} has an invalid movement type.")
        if event.deployment_id is None or event.event_key != (
            f"{event.deployment_id}:{event.kind}"
        ):
            flag(f"Journal entry {event.id} has an invalid deployment binding.")
        row = by_id.get(event.deployment_id) if event.deployment_id else None
        if row is None:
            flag(f"Journal entry {event.id} has no saved deployment.")
        else:
            expected_delta: Decimal | None = None
            if event.kind == "RESERVE":
                expected_delta = -row.budget
            elif event.kind == "RELEASE":
                expected_delta = row.budget
            elif event.kind == "CLOSE":
                state = row.state if isinstance(row.state, dict) else {}
                try:
                    expected_delta = row.budget + Decimal(str(state["realized_pnl"]))
                except (InvalidOperation, KeyError, TypeError, ValueError):
                    flag(f"Journal entry {event.id} has no valid close P&L.")
            if expected_delta is not None and event.delta != money(expected_delta):
                flag(f"Journal entry {event.id} differs from its saved deployment.")
        balance = money(balance + event.delta)
        if balance != event.cash_after:
            flag(f"Journal entry {event.id} does not match its running balance.")
    if balance != money(recorded_cash):
        flag("Journal ending cash differs from the saved equity wallet.")
    status: Literal["MATCHED", "LEGACY_CHECKPOINT", "NOT_STARTED", "MISMATCH"] = (
        "MISMATCH" if issue_count else
        "LEGACY_CHECKPOINT" if first.kind == "LEGACY_CHECKPOINT" else "MATCHED"
    )
    return EquityCashJournalReport(
        status=status, entry_count=len(events), checkpoint_cash=first.cash_after,
        recorded_cash=recorded_cash, journal_cash=balance,
        issue_count=issue_count, issues=issues,
    )
