"""Read-only consistency check for the equity/ETF paper wallet."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from packages.database.models import AuditEvent, EquityDeployment
from packages.paper_trading.equity import money, proceeds


@dataclass(frozen=True)
class EquityLedgerReconciliation:
    status: Literal["MATCHED", "MISMATCH"]
    initial_cash: Decimal
    recorded_cash: Decimal
    expected_cash: Decimal
    cash_difference: Decimal
    deployment_count: int
    reserved_count: int
    closed_count: int
    issue_count: int
    issues: list[str]
    audit_coverage: Literal["COMPLETE", "INCOMPLETE"]
    audit_issue_count: int
    audit_issues: list[str]


def _amount(value: object) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount.is_finite() else None


def reconcile_equity_ledger(
    *, user_id: object, initial_cash: Decimal, recorded_cash: Decimal,
    deployments: list[EquityDeployment], audit_events: list[AuditEvent],
) -> EquityLedgerReconciliation:
    """Recompute reserved cash and closed P&L from all saved deployments.

    This only checks internal records. Equity paper fills have no independent
    broker order ledger, so a MATCHED result cannot establish executable prices.
    """
    issues: list[str] = []
    issue_count = 0

    def flag(message: str) -> None:
        nonlocal issue_count
        issue_count += 1
        if len(issues) < 20:
            issues.append(message)

    expected = initial_cash
    reserved_count = closed_count = 0
    for row in deployments:
        if row.user_id != user_id:
            flag(f"Deployment {row.id} belongs to another user.")
            continue
        if row.budget <= 0 or row.risk_budget <= 0:
            flag(f"Deployment {row.id} has an invalid allocation or risk budget.")
        state = row.state if isinstance(row.state, dict) else {}
        if row.status in {"WAITING_FOR_ENTRY", "OPEN", "EXIT_REQUESTED"}:
            expected -= row.budget
            reserved_count += 1
            if row.status == "WAITING_FOR_ENTRY" and state.get("position") is not None:
                flag(f"Waiting deployment {row.id} already contains a position.")
            if row.status in {"OPEN", "EXIT_REQUESTED"} and not isinstance(
                state.get("position"), dict
            ):
                flag(f"Open deployment {row.id} has no saved position.")
            if state.get("realized_pnl") is not None:
                flag(f"Active deployment {row.id} has realized P&L.")
        elif row.status == "CLOSED":
            closed_count += 1
            pnl = _amount(state.get("realized_pnl"))
            if pnl is None:
                flag(f"Closed deployment {row.id} has no valid realized P&L.")
                continue
            expected += pnl
            position = state.get("position")
            evidence = state.get("outcome_evidence")
            if not isinstance(position, dict) or not isinstance(evidence, dict):
                flag(f"Closed deployment {row.id} lacks fill evidence.")
                continue
            bid = _amount(evidence.get("exit_bid"))
            cost = _amount(position.get("cost"))
            if bid is None or bid <= 0 or cost is None or cost <= 0:
                flag(f"Closed deployment {row.id} has invalid fill evidence.")
                continue
            try:
                fill, net = proceeds(position, bid)
                calculated = money(net - cost)
            except (InvalidOperation, KeyError, TypeError, ValueError, ArithmeticError):
                flag(f"Closed deployment {row.id} has unusable fill evidence.")
                continue
            if (calculated != pnl or _amount(state.get("exit_price")) != fill
                    or _amount(evidence.get("net_pnl")) != pnl):
                flag(f"Closed deployment {row.id} P&L differs from its saved fill.")
        elif row.status in {"CANCELLED", "EXPIRED"}:
            if state.get("position") is not None or state.get("realized_pnl") is not None:
                flag(f"Unfilled deployment {row.id} contains a position or P&L.")
        else:
            flag(f"Deployment {row.id} has an unknown status.")

    expected = money(expected)
    difference = money(recorded_cash - expected)
    if difference != 0:
        flag("Equity cash differs from initial cash minus active reservations plus closed P&L.")
    audit_issues: list[str] = []
    audit_issue_count = 0
    by_deployment: dict[str, dict[str, list[AuditEvent]]] = {}
    for event in audit_events:
        if event.actor_user_id != user_id or not event.correlation_id:
            continue
        by_deployment.setdefault(event.correlation_id, {}).setdefault(
            event.event_type, []
        ).append(event)

    def audit_flag(message: str) -> None:
        nonlocal audit_issue_count
        audit_issue_count += 1
        if len(audit_issues) < 20:
            audit_issues.append(message)

    terminal = {
        "OPEN": "entered", "EXIT_REQUESTED": "exit_requested",
        "CLOSED": "exited", "CANCELLED": "cancelled", "EXPIRED": "expired",
    }
    for row in deployments:
        if row.user_id != user_id:
            continue
        events = by_deployment.get(str(row.id), {})
        required = ["applied"]
        if row.status in terminal:
            required.append(terminal[row.status])
        if row.status in {"EXIT_REQUESTED", "CLOSED"}:
            required.append("entered")
        for name in required:
            kind = f"equity.deployment.{name}"
            matches = events.get(kind, [])
            if len(matches) != 1:
                audit_flag(
                    f"Deployment {row.id} has {len(matches)} {name} audit events; expected one."
                )
                continue
            details = matches[0].details
            if (not isinstance(details, dict)
                    or details.get("deployment_id") not in (None, str(row.id))):
                audit_flag(f"Deployment {row.id} {name} audit binding is inconsistent.")
            amount_key = {"applied": "allocation", "cancelled": "reserved_cash_released",
                          "expired": "reserved_cash_released"}.get(name)
            if amount_key and (not isinstance(details, dict)
                               or _amount(details.get(amount_key)) != row.budget):
                audit_flag(f"Deployment {row.id} {name} audit amount differs from allocation.")
            state = row.state if isinstance(row.state, dict) else {}
            if name == "exited" and (not isinstance(details, dict)
                                     or _amount(details.get("net_pnl"))
                                     != _amount(state.get("realized_pnl"))):
                audit_flag(f"Deployment {row.id} exit audit P&L differs from saved P&L.")

    return EquityLedgerReconciliation(
        status="MATCHED" if issue_count == 0 else "MISMATCH",
        initial_cash=initial_cash, recorded_cash=recorded_cash,
        expected_cash=expected, cash_difference=difference,
        deployment_count=len(deployments), reserved_count=reserved_count,
        closed_count=closed_count, issue_count=issue_count, issues=issues,
        audit_coverage="COMPLETE" if audit_issue_count == 0 else "INCOMPLETE",
        audit_issue_count=audit_issue_count, audit_issues=audit_issues,
    )
