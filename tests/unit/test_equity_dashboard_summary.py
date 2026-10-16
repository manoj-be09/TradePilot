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
