"""Exercise full-history account query without the host HTTP event-loop dependency."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.equity_routes import equity_dashboard
from packages.database.base import Base
from packages.database.models import EquityDeployment


def test_all_time_loss_breakeven_missing_and_account_scope():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner, other = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        values = ["10.00"] * 203 + ["-30.05", "0.00", None, "NaN"]
        for index, pnl in enumerate(values):
            session.add(EquityDeployment(
                user_id=owner, command_key=str(index), request_hash="0" * 64,
                provider="test", instrument_key="TEST", symbol="TEST", strategy="momentum",
                version="v1", budget=Decimal("1000"), risk_budget=Decimal("100"),
                status="CLOSED", state={"realized_pnl": pnl}, created_at=now, updated_at=now,
            ))
        session.add(EquityDeployment(
            user_id=other, command_key="other", request_hash="0" * 64,
            provider="test", instrument_key="TEST", symbol="TEST", strategy="momentum",
            version="v1", budget=Decimal("1000"), risk_budget=Decimal("100"),
            status="CLOSED", state={"realized_pnl": "99999"}, created_at=now, updated_at=now,
        ))
        session.commit()
        response = Response()
        report = equity_dashboard(SimpleNamespace(id=owner), session, response)
        stats = report["strategies"][0]
        assert report["scope"] == "all_time"
        assert len(report["cohorts"]) == 207
        assert all(row["configuration_evidence"] == "INCOMPLETE" for row in report["cohorts"])
        assert sum(row["closed_trades"] for row in report["cohorts"]) == 205
        assert stats["closed_trades"] == 205
        assert stats["winning_trades"] == 203
        assert stats["losing_trades"] == 1
        assert stats["breakeven_trades"] == 1
        assert stats["closed_unpriced"] == 2
        assert stats["realized_net"] == "1999.95"
        assert stats["average_loss"] == "-30.05"
        assert report["totals"]["average_loss"] == "-30.05"
        assert response.headers["Cache-Control"] == "no-store"
        isolated = equity_dashboard(SimpleNamespace(id=uuid4()), session, Response())
        assert isolated["totals"]["closed_trades"] == 0
        assert isolated["totals"]["average_loss"] is None
    engine.dispose()


def test_cohorts_separate_stock_version_policy_and_ignore_tests():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner = uuid4()
    now = datetime.now(timezone.utc)
    policy = {"atr_multiplier": "2", "minimum_distance_fraction": "0.01",
              "target_r": "2", "trail_activation_r": "1", "trail_distance_r": "1"}
    with Session(engine) as session:
        for index, (instrument, version, target, testing) in enumerate([
            ("A", "v1", "2", False), ("A", "v1", "2", False),
            ("B", "v1", "2", False), ("A", "v2", "2", False),
            ("A", "v1", "3", False), ("A", "v1", "2", True),
        ]):
            session.add(EquityDeployment(
                user_id=owner, command_key=str(index), request_hash="0" * 64,
                provider="test", instrument_key=instrument, symbol=instrument,
                strategy="momentum", version=version, budget=Decimal("1000"),
                risk_budget=Decimal("100"), status="CLOSED", created_at=now, updated_at=now,
                state={"realized_pnl": "10", "testing_only": testing, "quote_mode": "VERIFIED",
                       "position": {"calculation_version": "calc1", "exit_policy_version": "exit1",
                                    "exit_policy": policy | {"target_r": target}}},
            ))
        session.commit()
        report = equity_dashboard(SimpleNamespace(id=owner), session, Response())
        assert len(report["cohorts"]) == 4
        assert sorted(row["closed_trades"] for row in report["cohorts"]) == [1, 1, 1, 2]
        assert sum(Decimal(row["realized_net"]) for row in report["cohorts"]) == Decimal("50")
        assert report["totals"]["closed_trades"] == 6  # account totals retain test cash history
        assert all(row["configuration_evidence"] == "RECORDED" for row in report["cohorts"])
    engine.dispose()
