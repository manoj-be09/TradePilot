"""A balanced equity wallet must not hide missing or contradictory lifecycle evidence."""

from decimal import Decimal
from uuid import uuid4

from packages.database.models import AuditEvent, EquityDeployment
from packages.paper_trading.equity_ledger import reconcile_equity_ledger


def test_audit_coverage_is_separate_from_wallet_arithmetic():
    owner = uuid4()
    deployment = EquityDeployment(
        id=uuid4(), user_id=owner, budget=Decimal("10000"),
        risk_budget=Decimal("200"), status="WAITING_FOR_ENTRY", state={},
    )
    applied = AuditEvent(
        actor_user_id=owner, correlation_id=str(deployment.id),
        event_type="equity.deployment.applied", details={"allocation": "10000.00"},
    )

    def check(events):
        return reconcile_equity_ledger(
            user_id=owner, initial_cash=Decimal("1000000"),
            recorded_cash=Decimal("990000"), deployments=[deployment],
            audit_events=events,
        )

    complete = check([applied])
    assert complete.status == "MATCHED"
    assert complete.audit_coverage == "COMPLETE"

    missing = check([])
    assert missing.status == "MATCHED"
    assert missing.audit_coverage == "INCOMPLETE"
    assert missing.audit_issue_count == 1

    applied.details = {"allocation": "9999.00"}
    tampered = check([applied])
    assert tampered.status == "MATCHED"
    assert tampered.audit_coverage == "INCOMPLETE"
    assert "differs" in tampered.audit_issues[0]

    applied.details = {"allocation": "10000.00", "deployment_id": str(uuid4())}
    wrong_binding = check([applied])
    assert wrong_binding.status == "MATCHED"
    assert wrong_binding.audit_coverage == "INCOMPLETE"
    assert any("binding" in issue for issue in wrong_binding.audit_issues)


def test_closed_equity_deployment_requires_entry_and_exit_audit():
    owner = uuid4()
    deployment = EquityDeployment(
        id=uuid4(), user_id=owner, budget=Decimal("10000"),
        risk_budget=Decimal("200"), status="CLOSED",
        state={"realized_pnl": "10.00"},
    )
    events = [AuditEvent(
        actor_user_id=owner, correlation_id=str(deployment.id),
        event_type="equity.deployment.applied", details={"allocation": "10000.00"},
    )]
    result = reconcile_equity_ledger(
        user_id=owner, initial_cash=Decimal("1000000"),
        recorded_cash=Decimal("1000010"), deployments=[deployment],
        audit_events=events,
    )
    assert result.audit_coverage == "INCOMPLETE"
    assert result.audit_issue_count == 2
    assert any("entered" in issue for issue in result.audit_issues)
    assert any("exited" in issue for issue in result.audit_issues)
