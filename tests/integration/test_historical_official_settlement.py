"""Opt-in network acceptance: synthetic positions, real NSE archive evidence."""

import asyncio
import json
import os
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_paper_trading import FakeUpstoxProvider, _token

from packages.database.models import PaperAccount, PaperOrder, PaperTrade
from packages.paper_trading.ledger_reconciliation import reconcile_paper_ledger
from packages.paper_trading.official_index_close import fetch_nifty_close


@pytest.mark.skipif(
    not os.getenv("PAPER_HISTORICAL_EXPIRY_DATE"),
    reason="Explicit past expiry date required for live NSE archive lookup",
)
@pytest.mark.parametrize("strategy", ["long_call", "long_put"])
def test_real_archive_synthetic_position_settlement(client, monkeypatch, strategy):
    expiry = date.fromisoformat(os.environ["PAPER_HISTORICAL_EXPIRY_DATE"])
    assert expiry < date.today()
    evidence = asyncio.run(fetch_nifty_close(expiry))
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    opened = client.post("/paper/trades", headers={
        **headers, "Idempotency-Key": f"historical_{uuid4().hex}",
    }, json={
        "strategy_key": strategy, "expiry": provider.expiry.isoformat(),
        "reference_strike": "25000", "lots": 1,
    })
    assert opened.status_code == 201, opened.text
    trade_id = UUID(opened.json()["id"])
    factory = client.app.state.test_session_factory
    with factory() as session:
        # Explicit synthetic historical position; never production history.
        session.get(PaperTrade, trade_id).expiry = expiry
        session.commit()
    endpoint = f"/paper/trades/{trade_id}/expiry-settlement"
    body = {
        "settlement_date": expiry.isoformat(),
        "final_index_value": str(evidence.close),
        "source_reference": evidence.source_url,
        "archive_sha256": evidence.sha256, "confirmed_final": True,
    }
    # Real settlement route re-fetches the official archive and checks its hash.
    settled = client.post(endpoint, headers=headers, json=body)
    assert settled.status_code == 200, settled.text
    assert client.post(endpoint, headers=headers, json=body).status_code == 200
    with factory() as session:
        trade = session.get(PaperTrade, trade_id)
        orders = list(session.scalars(select(PaperOrder)))
        account = session.get(PaperAccount, trade.account_id)
        assert trade.status == "CLOSED" and trade.exit_reason == "EXPIRY_SETTLEMENT"
        assert len(orders) == 2
        close = next(order for order in orders if order.action == "CLOSE")
        # Independent intrinsic calculation, not the settlement helper.
        expected = sum((
            max(
                evidence.close - Decimal(leg["strike"])
                if leg["option_type"] == "CE" else
                Decimal(leg["strike"]) - evidence.close,
                Decimal(0),
            ) * leg["quantity_units"] * (1 if leg["side"] == "BUY" else -1)
            for leg in trade.legs["items"]
        ), Decimal(0)).quantize(Decimal("0.01"))
        assert close.net_cash_flow == expected
        report = reconcile_paper_ledger(account, [trade], orders)
        assert report.status == "MATCHED" and report.cash_difference == 0
        assert close.fill_snapshot["settlement_reference"]["archive_sha256"] == evidence.sha256
        print(json.dumps({
            "scope": "synthetic position / actual NSE archive / disposable SQLite",
            "strategy": strategy, "expiry": str(expiry),
            "official_close": str(evidence.close), "source": evidence.source_url,
            "sha256": evidence.sha256, "settlement_cash": str(expected),
            "realized_pnl": str(trade.realized_pnl),
            "orders_after_repeat": len(orders), "ledger": report.status,
            "cash_difference": str(report.cash_difference),
        }))
