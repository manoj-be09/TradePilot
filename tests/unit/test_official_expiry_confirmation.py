"""A reviewed NSE-based paper settlement must match the current archive."""

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api import main
from apps.api.paper_schemas import PaperExpirySettlementRequest
from packages.database import models  # noqa: F401
from packages.database.base import Base
from packages.database.models import PaperAccount, PaperOrder, PaperTrade
from packages.paper_trading.official_index_close import (
    OfficialCloseUnavailable,
    OfficialIndexClose,
    archive_url,
)

EXPIRY = date(2020, 1, 1)
URL = archive_url(EXPIRY)
DIGEST = "a" * 64


def _finish_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    pytest.fail("Fake archive fetch unexpectedly suspended")


def _body(**changes):
    values = {
        "settlement_date": EXPIRY,
        "final_index_value": Decimal("25100.00"),
        "source_reference": URL,
        "archive_sha256": DIGEST,
        "confirmed_final": True,
    }
    return PaperExpirySettlementRequest(**(values | changes))


def test_matching_archive_can_be_confirmed_and_changes_fail_closed(monkeypatch):
    current = OfficialIndexClose(
        close=Decimal("25100.00"), source_url=URL, sha256=DIGEST,
        retrieved_at=datetime.now(timezone.utc),
    )

    async def fetch(day):
        assert day == EXPIRY
        return current

    monkeypatch.setattr(main, "fetch_nifty_close", fetch)
    assert _finish_immediate(main._check_nse_settlement_reference(_body())) is None
    for changes in (
        {"archive_sha256": "b" * 64},
        {"final_index_value": Decimal("25101.00")},
    ):
        with pytest.raises(HTTPException) as stale:
            _finish_immediate(main._check_nse_settlement_reference(_body(**changes)))
        assert stale.value.status_code == 409


def test_missing_archive_fingerprint_or_feed_blocks_nse_confirmation(monkeypatch):
    async def unavailable(_day):
        raise OfficialCloseUnavailable("private upstream detail")

    monkeypatch.setattr(main, "fetch_nifty_close", unavailable)
    with pytest.raises(HTTPException) as missing_hash:
        _finish_immediate(main._check_nse_settlement_reference(
            _body(archive_sha256=None),
        ))
    assert missing_hash.value.status_code == 422
    with pytest.raises(HTTPException) as unavailable_archive:
        _finish_immediate(main._check_nse_settlement_reference(_body()))
    assert unavailable_archive.value.status_code == 409
    assert "private upstream detail" not in unavailable_archive.value.detail


def test_manual_source_cannot_claim_an_nse_archive_fingerprint(monkeypatch):
    async def unexpected_fetch(_day):
        pytest.fail("Manual settlement source must not fetch the NSE archive")

    monkeypatch.setattr(main, "fetch_nifty_close", unexpected_fetch)
    manual = {"source_reference": "Reviewed alternative settlement report"}
    assert _finish_immediate(main._check_nse_settlement_reference(
        _body(**manual, archive_sha256=None),
    )) is None
    with pytest.raises(HTTPException) as mismatched_source:
        _finish_immediate(main._check_nse_settlement_reference(_body(**manual)))
    assert mismatched_source.value.status_code == 422


@pytest.mark.parametrize("underlying", ["NIFTY", "BANKNIFTY"])
def test_record_rechecks_archive_before_committing_and_preserves_idempotency(
    monkeypatch, underlying,
):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner = uuid4()
    trade_id = uuid4()

    async def official_close(_day):
        return OfficialIndexClose(
            close=Decimal("25100.00"), source_url=URL, sha256=DIGEST,
            retrieved_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(main, "fetch_nifty_close", official_close)

    async def selected_close(day, symbol):
        assert symbol == "BANKNIFTY"
        return await official_close(day)

    monkeypatch.setattr(main, "fetch_index_close", selected_close)
    try:
        with Session(engine) as session:
            account = PaperAccount(
                id=uuid4(), user_id=owner, initial_cash=Decimal("1000000.00"),
                cash_balance=Decimal("997475.00"),
            )
            trade = PaperTrade(
                id=trade_id, account_id=account.id, user_id=owner,
                strategy_key="long_call", strategy_name="Long call", underlying=underlying,
                expiry=EXPIRY, reference_strike=Decimal("25000"), lots=1, lot_size=25,
                max_loss=Decimal("2525.00"), entry_cash_flow=Decimal("-2525.00"),
                status="OPEN", entry_rule_snapshot={} if underlying == "NIFTY" else {
                    "option_index": {
                        "symbol": "BANKNIFTY", "name": "Nifty Bank",
                        "instrument_key": "NSE_INDEX|Nifty Bank", "exchange": "NSE",
                        "derivatives_exchange": "NFO",
                    },
                }, legs={"items": [{
                    "instrument_key": "NSE_FO|TEST", "trading_symbol": "NIFTY TEST CE",
                    "option_type": "CE", "side": "BUY", "strike": "25000",
                    "quantity_units": 25, "entry_price": "101", "entry_bid": "100",
                    "entry_ask": "101",
                }]},
            )
            session.add_all([account, trade, PaperOrder(
                account_id=account.id, trade_id=trade_id, user_id=owner,
                action="OPEN", status="EXECUTED",
                net_cash_flow=Decimal("-2525.00"), fill_snapshot={"paper_only": True},
            )])
            session.commit()
            user = SimpleNamespace(id=owner)
            with pytest.raises(HTTPException) as stale:
                _finish_immediate(main.reconcile_paper_expiry_settlement(
                    trade_id, _body(archive_sha256="b" * 64), user, session,
                ))
            assert stale.value.status_code == 409
            assert session.get(PaperTrade, trade_id).status == "OPEN"

            recorded = _finish_immediate(main.reconcile_paper_expiry_settlement(
                trade_id, _body(), user, session,
            ))
            assert recorded.realized_pnl == Decimal("-25.00")
            assert recorded.status == "CLOSED"
            assert session.get(PaperAccount, account.id).cash_balance == Decimal("999975.00")
            closes = list(session.scalars(select(PaperOrder).where(
                PaperOrder.trade_id == trade_id, PaperOrder.action == "CLOSE",
            )))
            assert len(closes) == 1
            assert closes[0].fill_snapshot["settlement_reference"]["archive_sha256"] == DIGEST
            assert closes[0].fill_snapshot["underlying"] == underlying

            async def unavailable(_day):
                raise OfficialCloseUnavailable("Archive temporarily unavailable")

            monkeypatch.setattr(main, "fetch_nifty_close", unavailable)
            repeat = _finish_immediate(main.reconcile_paper_expiry_settlement(
                trade_id, _body(), user, session,
            ))
            assert repeat.id == recorded.id
            assert len(list(session.scalars(select(PaperOrder).where(
                PaperOrder.trade_id == trade_id, PaperOrder.action == "CLOSE",
            )))) == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
