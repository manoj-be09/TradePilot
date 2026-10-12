"""Fail closed on inconsistent F&O paper cash before a simulated mutation."""

from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.main import _require_consistent_paper_ledger
from packages.database import models  # noqa: F401
from packages.database.base import Base
from packages.database.models import PaperAccount
from packages.paper_trading.transactions import (
    PaperLedgerMismatchError,
    assert_consistent_ledger,
    lock_account,
)


def test_locked_paper_account_blocks_drifted_cash():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner = uuid4()
    try:
        with Session(engine) as session:
            account = PaperAccount(
                user_id=owner, initial_cash=Decimal("1000000.00"),
                cash_balance=Decimal("1000000.00"),
            )
            session.add(account)
            session.commit()
            locked = lock_account(session, owner)
            assert locked is not None
            assert_consistent_ledger(session, locked)
            session.rollback()

            account = session.get(PaperAccount, account.id)
            assert account is not None
            account.cash_balance = Decimal("999999.00")
            session.commit()
            locked = lock_account(session, owner)
            assert locked is not None
            with pytest.raises(PaperLedgerMismatchError, match="ledger mismatch"):
                assert_consistent_ledger(session, locked)
            with pytest.raises(HTTPException) as blocked:
                _require_consistent_paper_ledger(session, locked)
            assert blocked.value.status_code == 409
            assert session.get(PaperAccount, locked.id).cash_balance == Decimal("999999.00")
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
