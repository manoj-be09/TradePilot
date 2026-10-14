from uuid import uuid4

import pytest
from test_paper_trading import FakeUpstoxProvider, _token

from apps.api.settings import get_settings
from packages.market_data.contracts import ExchangeSession
from packages.market_data.index_catalog import OptionIndex


@pytest.mark.parametrize("symbol,exchange,venue", [
    ("SENSEX", "BSE", "BFO"), ("BANKNIFTY", "NSE", "NFO"),
])
def test_selected_index_entry_close_and_ledger(client, monkeypatch, symbol, exchange, venue):
    provider = FakeUpstoxProvider()
    for contract in provider.contracts:
        contract.instrument_key = contract.instrument_key.replace("NSE_FO|", exchange + "_FO|")
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = {**_token(client), "Idempotency-Key": str(uuid4())}
    payload = {"underlying": symbol, "strategy_key": "long_call", "lots": 1,
               "reference_strike": "25000", "expiry": provider.expiry.isoformat()}
    blocked = client.post("/paper/trades", headers=headers, json=payload)
    assert blocked.status_code == 409, blocked.text
    monkeypatch.setattr(get_settings(), "paper_index_execution_enabled", True)
    index = OptionIndex(symbol=symbol, instrument_key=exchange + "_INDEX|" + symbol,
                        name=symbol, exchange=exchange, derivatives_exchange=venue)

    async def resolve(underlying):
        assert underlying == symbol
        return index

    selected_venue = "NFO" if venue == "BFO" else "BFO"

    async def scheduled(underlying, day):
        assert underlying == symbol
        return ExchangeSession(selected_venue, provider.session_open, provider.session_close)

    monkeypatch.setattr("apps.api.main.resolve_option_index", resolve)
    monkeypatch.setattr(provider, "get_index_option_session", scheduled)
    wrong = client.post("/paper/trades", headers=headers, json=payload)
    assert wrong.status_code == 409, wrong.text
    selected_venue = venue
    opened = client.post("/paper/trades", headers=headers, json=payload)
    assert opened.status_code == 201, opened.text
    trade = opened.json()
    assert trade["underlying"] == symbol
    assert trade["entry_rule_snapshot"]["option_index"] == index.model_dump()
    assert client.post("/paper/trades", headers=headers, json=payload).json()["id"] == trade["id"]
    closed = client.post(f"/paper/trades/{trade['id']}/close", headers=headers)
    assert closed.status_code == 200, closed.text
    assert len(client.get("/paper/orders", headers=headers).json()) == 2
    assert client.get("/paper/account/ledger-reconciliation", headers=headers).json()[
        "status"
    ] == "MATCHED"
