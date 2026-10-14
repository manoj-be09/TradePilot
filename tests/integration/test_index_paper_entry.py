import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from test_paper_trading import FakeUpstoxProvider, _token

from apps.api.settings import get_settings
from apps.paper_worker.main import run_cycle
from packages.market_data.contracts import ExchangeSession
from packages.market_data.index_catalog import OptionIndex
from packages.paper_trading.session import exchange_today


@pytest.mark.parametrize("symbol,exchange,venue", [
    ("SENSEX", "BSE", "BFO"), ("BANKNIFTY", "NSE", "NFO"),
])
@pytest.mark.parametrize("entry_mode", ["manual", "automatic"])
@pytest.mark.parametrize("expiry_day", [False, True])
def test_selected_index_entry_close_and_ledger(
    client, monkeypatch, symbol, exchange, venue, entry_mode, expiry_day
):
    provider = FakeUpstoxProvider()
    if expiry_day:
        provider.expiry = exchange_today()
    for contract in provider.contracts:
        contract.expiry = provider.expiry
        contract.instrument_key = contract.instrument_key.replace("NSE_FO|", exchange + "_FO|")
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = {**_token(client), "Idempotency-Key": str(uuid4())}
    payload = {"underlying": symbol, "strategy_key": "long_call", "lots": 1,
               "reference_strike": "25000", "expiry": provider.expiry.isoformat()}
    route = "/paper/trades" if entry_mode == "manual" else "/paper/deployments"
    if entry_mode == "automatic":
        payload.update(entry_conditions=[{
            "metric": "underlying_spot", "operator": "GT", "threshold": "25000",
        }], stop_loss_amount="100", target_profit_amount="1", trailing_stop_amount="50")
    blocked = client.post(route, headers=headers, json=payload)
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
    wrong = client.post(route, headers=headers, json=payload)
    if entry_mode == "manual":
        assert wrong.status_code == 409, wrong.text
    else:
        assert wrong.status_code == 201, wrong.text
        asyncio.run(run_cycle(client.app.state.test_session_factory, provider, get_settings()))
        assert client.get("/paper/orders", headers=headers).json() == []
    selected_venue = venue
    opened = client.post(route, headers=headers, json=payload)
    assert opened.status_code == 201, opened.text
    if entry_mode == "automatic":
        assert opened.json()["underlying"] == symbol
        asyncio.run(run_cycle(client.app.state.test_session_factory, provider, get_settings()))
        trade = client.get("/paper/trades", headers=headers).json()["trades"][0]
    else:
        trade = opened.json()
    assert trade["underlying"] == symbol
    assert trade["entry_rule_snapshot"]["option_index"] == index.model_dump()
    assert client.post(route, headers=headers, json=payload).json()["id"] == opened.json()["id"]
    if entry_mode == "automatic":
        provider.price_adjustment = Decimal("10")
        for _ in range(2):
            asyncio.run(run_cycle(client.app.state.test_session_factory, provider, get_settings()))
        final_trade = client.get("/paper/trades", headers=headers).json()["trades"][0]
        assert final_trade["status"] == "CLOSED"
    else:
        closed = client.post(f"/paper/trades/{trade['id']}/close", headers=headers)
        assert closed.status_code == 200, closed.text
    assert len(client.get("/paper/orders", headers=headers).json()) == 2
    selected_stats = client.get(f"/paper/strategies?underlying={symbol}", headers=headers).json()
    nifty_stats = client.get("/paper/strategies", headers=headers).json()
    assert next(item for item in selected_stats if item["key"] == "long_call")[
        "closed_trade_count"
    ] == 1
    assert next(item for item in nifty_stats if item["key"] == "long_call")[
        "closed_trade_count"
    ] == 0
    assert client.get("/paper/account/ledger-reconciliation", headers=headers).json()[
        "status"
    ] == "MATCHED"
