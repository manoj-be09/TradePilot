"""Regression tests for defects confirmed in the September 26 review."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from test_paper_trading import FakeUpstoxProvider, _token


def payload(expiry):
    return {
        "strategy_key": "long_call",
        "expiry": expiry.isoformat(),
        "reference_strike": "25000",
        "lots": 1,
        "stop_loss_amount": "100",
    }


def test_delete_preflight_is_allowed(client):
    response = client.options(
        "/paper/strategy-presets/00000000-0000-0000-0000-000000000001",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert "DELETE" in response.headers.get("access-control-allow-methods", "")


def test_duplicate_entry_intent_returns_original_trade(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = {**_token(client), "Idempotency-Key": "review-identical-intent"}
    first = client.post("/paper/trades", headers=headers, json=payload(provider.expiry))
    second = client.post("/paper/trades", headers=headers, json=payload(provider.expiry))
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert client.get("/paper/account", headers=headers).json()["open_trade_count"] == 1


def test_stale_exchange_snapshot_cannot_fill(client, monkeypatch):
    class StaleProvider(FakeUpstoxProvider):
        async def get_option_chain(self, underlying, expiry):
            chain = await super().get_option_chain(underlying, expiry)
            stale = datetime.now(timezone.utc) - timedelta(hours=1)
            chain.as_of = stale
            for contract in chain.contracts:
                contract.quote.timestamp = stale
            return chain

    provider = StaleProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = {**_token(client), "Idempotency-Key": "review-entry-001"}
    opened = client.post("/paper/trades", headers=headers, json=payload(provider.expiry))
    assert opened.status_code == 409
    assert "stale" in opened.json()["detail"]


def test_multi_expiry_exit_records_own_snapshot(client, monkeypatch):
    class MultiExpiryProvider(FakeUpstoxProvider):
        def __init__(self):
            super().__init__()
            self.expiries = [date.today() + timedelta(days=30), date.today() + timedelta(days=37)]
            now = datetime.now(timezone.utc)
            self.times = {self.expiries[0]: now - timedelta(seconds=5), self.expiries[1]: now}

        async def get_option_contracts(self, underlying="NIFTY", expiry=None):
            return [c.model_copy(update={"expiry": expiry}) for c in self.contracts]

        async def get_option_chain(self, underlying, expiry):
            chain = await super().get_option_chain(underlying, expiry)
            chain.as_of = self.times[expiry]
            for contract in chain.contracts:
                contract.quote.timestamp = self.times[expiry]
            return chain

    provider = MultiExpiryProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = {**_token(client), "Idempotency-Key": "review-entry-001"}
    first = client.post("/paper/trades", headers=headers, json=payload(provider.expiries[0]))
    second = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": "review-entry-002"},
        json=payload(provider.expiries[1]),
    )
    assert first.status_code == second.status_code == 201
    provider.price_adjustment = Decimal(-10)
    monitor = client.post("/paper/monitor", headers=headers)
    assert len(monitor.json()["closed_trade_ids"]) == 2
    orders = client.get("/paper/orders", headers=headers).json()
    first_exit = next(
        o for o in orders if o["trade_id"] == first.json()["id"] and o["action"] == "CLOSE"
    )
    snapshot = first_exit["fill_snapshot"]
    assert snapshot["market_as_of"] == provider.times[provider.expiries[0]].isoformat()
    assert (
        snapshot["fills"][0]["quote_timestamp"] == provider.times[provider.expiries[0]].isoformat()
    )
