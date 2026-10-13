"""Future cash movements are durable; legacy history is never fabricated."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.equity_routes import stop
from apps.api.services.equity_runtime import lock_wallet
from packages.database.base import Base
from packages.database.models import EquityAccount, EquityCashEvent, EquityDeployment, User
from packages.paper_trading.equity_cash_journal import (
    EquityCashJournalMismatch,
    ensure_equity_cash_journal,
    reconcile_equity_cash_journal,
    record_equity_cash_event,
)


def test_new_wallet_journal_reconciles_reserve_and_close_then_detects_tampering():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner, deployment = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(User(id=owner, email="journal-new@example.test", password_hash="unused"))
        account = EquityAccount(
            user_id=owner, initial_cash=Decimal("1000"), cash=Decimal("1000")
        )
        session.add(account)
        session.flush()
        ensure_equity_cash_journal(session, account, created=True)
        account.cash -= Decimal("100")
        record_equity_cash_event(
            session, account, deployment, kind="RESERVE", delta=Decimal("-100")
        )
        account.cash += Decimal("110")
        record_equity_cash_event(
            session, account, deployment, kind="CLOSE", delta=Decimal("110")
        )
        session.commit()

    with Session(engine) as session:
        rows = list(session.scalars(select(EquityCashEvent).order_by(EquityCashEvent.id)))
        saved = EquityDeployment(
            id=deployment, user_id=owner, budget=Decimal("100"),
            risk_budget=Decimal("10"), status="CLOSED",
            state={"realized_pnl": "10.00"},
        )
        report = reconcile_equity_cash_journal(
            user_id=owner, initial_cash=Decimal("1000"),
            recorded_cash=Decimal("1010"), events=rows, deployments=[saved],
        )
        assert report.status == "MATCHED"
        assert report.entry_count == 3
        rows[1].cash_after = Decimal("901")
        corrupted = reconcile_equity_cash_journal(
            user_id=owner, initial_cash=Decimal("1000"),
            recorded_cash=Decimal("1010"), events=rows, deployments=[saved],
        )
        assert corrupted.status == "MISMATCH"
        assert any("running balance" in issue for issue in corrupted.issues)
    engine.dispose()


def test_wallet_lock_rejects_unjournaled_cash_change():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner = uuid4()
    with Session(engine) as session:
        session.add(User(id=owner, email="journal-drift@example.test", password_hash="unused"))
        account = EquityAccount(
            user_id=owner, initial_cash=Decimal("1000"), cash=Decimal("1000")
        )
        session.add(account)
        session.flush()
        ensure_equity_cash_journal(session, account, created=True)
        session.commit()
        account.cash += Decimal("1")
        session.commit()

    with Session(engine) as session:
        with pytest.raises(EquityCashJournalMismatch, match="needs review"):
            lock_wallet(session, owner)
        session.rollback()
        assert session.get(EquityAccount, owner).cash == Decimal("1001")
        assert session.scalar(select(EquityCashEvent).where(
            EquityCashEvent.user_id == owner,
        )).cash_after == Decimal("1000")
    engine.dispose()


def test_new_wallet_rejects_closed_deployment_with_missing_cash_events():
    owner = uuid4()
    opening = EquityCashEvent(
        user_id=owner, event_key="opening", kind="INITIAL_FUNDING",
        delta=Decimal("1000"), cash_after=Decimal("1000"),
    )
    closed = EquityDeployment(
        id=uuid4(), user_id=owner, budget=Decimal("100"),
        risk_budget=Decimal("10"), status="CLOSED",
        state={"realized_pnl": "0.00"},
    )
    report = reconcile_equity_cash_journal(
        user_id=owner, initial_cash=Decimal("1000"),
        recorded_cash=Decimal("1000"), events=[opening], deployments=[closed],
    )
    assert report.status == "MISMATCH"
    assert "incomplete cash journal" in report.issues[0]


def test_legacy_checkpoint_cannot_hide_existing_wallet_mismatch():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner = uuid4()
    with Session(engine) as session:
        session.add(User(
            id=owner, email="journal-legacy-drift@example.test", password_hash="unused"
        ))
        account = EquityAccount(
            user_id=owner, initial_cash=Decimal("1000"), cash=Decimal("999")
        )
        session.add(account)
        session.flush()
        ensure_equity_cash_journal(session, account, created=False)
        session.commit()

    with Session(engine) as session:
        user = session.get(User, owner)
        assert user is not None
        with pytest.raises(HTTPException) as error:
            stop(uuid4(), user, session)
        assert error.value.status_code == 409
        assert "journal needs review" in error.value.detail
    engine.dispose()


def test_legacy_wallet_starts_from_labeled_checkpoint_without_rewriting_balance():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner, deployment = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(User(id=owner, email="journal-old@example.test", password_hash="unused"))
        account = EquityAccount(
            user_id=owner, initial_cash=Decimal("1000"), cash=Decimal("900")
        )
        session.add(account)
        session.commit()
        ensure_equity_cash_journal(session, account, created=False)
        ensure_equity_cash_journal(session, account, created=False)
        account.cash += Decimal("100")
        record_equity_cash_event(
            session, account, deployment, kind="RELEASE", delta=Decimal("100")
        )
        now = datetime.now(timezone.utc)
        session.add(EquityDeployment(
            id=deployment, user_id=owner, command_key=str(uuid4()),
            request_hash="0" * 64, provider="test", instrument_key="EQ|TEST",
            symbol="TEST", strategy="trend", version="v1",
            budget=Decimal("100"), risk_budget=Decimal("10"),
            status="CANCELLED", state={}, created_at=now, updated_at=now,
        ))
        session.commit()
    with Session(engine) as session:
        rows = list(session.scalars(select(EquityCashEvent).order_by(EquityCashEvent.id)))
        assert len(rows) == 2
        assert rows[0].kind == "LEGACY_CHECKPOINT" and rows[0].delta == 0
        saved = session.get(EquityDeployment, deployment)
        assert saved is not None
        report = reconcile_equity_cash_journal(
            user_id=owner, initial_cash=Decimal("1000"),
            recorded_cash=Decimal("1000"), events=rows, deployments=[saved],
        )
        assert report.status == "LEGACY_CHECKPOINT"
        assert report.checkpoint_cash == Decimal("900")
        assert lock_wallet(session, owner).cash == Decimal("1000")
        session.rollback()
    engine.dispose()
