"""Direct checks for the read-only NSE expiry cash preview."""

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from apps.api import main
from packages.paper_trading.official_index_close import OfficialCloseUnavailable, OfficialIndexClose


def _finish_immediate(coroutine):
    """Run a fake I/O coroutine without Windows' event-loop socketpair."""
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    pytest.fail("The fake archive fetch unexpectedly suspended")


def _trade():
    return SimpleNamespace(
        id=uuid4(), user_id=uuid4(), status="OPEN", underlying="NIFTY",
        expiry=date(2020, 1, 1), entry_cash_flow=Decimal("-2525.00"),
        legs={"items": [{
            "instrument_key": "NSE_FO|TEST", "option_type": "CE", "side": "BUY",
            "strike": "25000", "quantity_units": 25,
        }]},
    )


def test_official_preview_values_cash_without_mutating_trade(monkeypatch):
    trade = _trade()
    user = SimpleNamespace(id=trade.user_id)
    session = SimpleNamespace(scalar=lambda _statement: trade)
    response = Response()

    async def official_close(day):
        assert day == trade.expiry
        return OfficialIndexClose(
            close=Decimal("25100.00"),
            source_url="https://archives.nseindia.com/content/indices/example.csv",
            sha256="a" * 64,
            retrieved_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(main, "fetch_nifty_close", official_close)
    result = _finish_immediate(main.paper_official_expiry_settlement_preview(
        trade.id, user, session, response,
    ))

    assert response.headers["cache-control"] == "no-store"
    assert result.status == "available"
    assert result.archive_sha256 == "a" * 64
    assert result.preview is not None
    assert result.preview.final_index_value == Decimal("25100.00")
    assert result.preview.settlement_cash_flow == Decimal("2500.00")
    assert result.preview.realized_pnl == Decimal("-25.00")
    assert result.preview.source_reference == result.source_url
    assert "candidate" in result.preview.warning
    assert trade.status == "OPEN"


def test_official_preview_unavailable_does_not_estimate(monkeypatch):
    trade = _trade()

    async def unavailable(_day):
        raise OfficialCloseUnavailable("NSE archive missing")

    monkeypatch.setattr(main, "fetch_nifty_close", unavailable)
    result = _finish_immediate(main.paper_official_expiry_settlement_preview(
        trade.id, SimpleNamespace(id=trade.user_id),
        SimpleNamespace(scalar=lambda _statement: trade), Response(),
    ))
    assert result.status == "unavailable"
    assert result.preview is None
    assert result.reason == "NSE archive missing"
    assert trade.status == "OPEN"


def test_official_preview_rejects_non_owned_or_closed_trade(monkeypatch):
    async def unexpected_fetch(_day):
        pytest.fail("An ineligible trade must not fetch the archive")

    monkeypatch.setattr(main, "fetch_nifty_close", unexpected_fetch)
    trade = _trade()
    with pytest.raises(HTTPException) as not_found:
        _finish_immediate(main.paper_official_expiry_settlement_preview(
            trade.id, SimpleNamespace(id=trade.user_id),
            SimpleNamespace(scalar=lambda _statement: None), Response(),
        ))
    assert not_found.value.status_code == 404

    trade.status = "CLOSED"
    with pytest.raises(HTTPException) as closed:
        _finish_immediate(main.paper_official_expiry_settlement_preview(
            trade.id, SimpleNamespace(id=trade.user_id),
            SimpleNamespace(scalar=lambda _statement: trade), Response(),
        ))
    assert closed.value.status_code == 409


def test_selected_index_reference_requires_pin_and_never_fetches_nifty(monkeypatch):
    trade = _trade()
    trade.underlying = "BANKNIFTY"
    trade.entry_rule_snapshot = {"option_index": {
        "symbol": "BANKNIFTY", "name": "Nifty Bank",
        "instrument_key": "NSE_INDEX|Nifty Bank", "exchange": "NSE",
        "derivatives_exchange": "NFO",
    }}

    async def selected_close(day, underlying):
        assert (day, underlying) == (trade.expiry, "BANKNIFTY")
        return OfficialIndexClose(
            close=Decimal("50100"), source_url="https://archives.nseindia.com/test.csv",
            sha256="b" * 64, retrieved_at=datetime.now(timezone.utc), index_name="Nifty Bank",
        )

    monkeypatch.setattr(main, "fetch_index_close", selected_close)
    args = (trade.id, SimpleNamespace(id=trade.user_id),
            SimpleNamespace(scalar=lambda _: trade), Response())
    result = _finish_immediate(main.paper_official_index_close_reference(*args))
    assert result.underlying == "BANKNIFTY"
    assert result.index_name == "Nifty Bank"
    assert result.daily_close == Decimal("50100")
    assert "Nifty Bank" in result.warning
    assert trade.status == "OPEN"
    trade.entry_rule_snapshot = {}
    with pytest.raises(HTTPException) as missing_pin:
        _finish_immediate(main.paper_official_index_close_reference(*args))
    assert missing_pin.value.status_code == 409
    trade.underlying = "SENSEX"
    with pytest.raises(HTTPException) as unsupported:
        _finish_immediate(main.paper_official_index_close_reference(*args))
    assert unsupported.value.status_code == 409
