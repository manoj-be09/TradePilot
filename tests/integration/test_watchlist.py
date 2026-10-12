from datetime import datetime, timezone

import httpx

from packages.market_data.contracts import DiscoveredInstrument, HistoricalBar, Quote
from packages.market_data.upstox import UpstoxMarketDataProvider


def _headers(client, email: str) -> dict[str, str]:
    token = client.post(
        "/auth/register", json={"email": email, "password": "a-strong-test-password"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_watchlist_is_account_scoped_idempotent_and_provider_verified(client, monkeypatch):
    alice = _headers(client, "watch-alice@example.com")
    bob = _headers(client, "watch-bob@example.com")
    search_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal search_count
        search_count += 1
        assert request.url.path == "/v2/instruments/search"
        assert request.url.params["query"] == "RELIANCE"
        return httpx.Response(200, json={"status": "success", "data": [{
            "instrument_key": "NSE_EQ|INE002A01018", "trading_symbol": "RELIANCE",
            "exchange": "NSE", "segment": "NSE_EQ", "instrument_type": "EQ",
            "name": "Reliance Industries", "lot_size": 1, "tick_size": 0.05,
        }]})

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    body = {
        "query": "RELIANCE", "instrument_key": "NSE_EQ|INE002A01018",
        "exchange": "NSE", "segment": "EQ",
    }
    assert client.get("/watchlist").status_code == 401
    rejected = client.post(
        "/watchlist", json={**body, "instrument_key": "NSE_EQ|forged"}, headers=alice
    )
    assert rejected.status_code == 422
    saved = client.post("/watchlist", json=body, headers=alice)
    assert saved.status_code == 201, saved.text
    item = saved.json()
    assert item["trading_symbol"] == "RELIANCE"
    assert item["name"] == "Reliance Industries"
    assert "test-token" not in saved.text
    duplicate = client.post("/watchlist", json=body, headers=alice)
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == item["id"]
    assert search_count == 2  # Forged key and first save; retry needs no provider request.
    assert [row["id"] for row in client.get("/watchlist", headers=alice).json()] == [item["id"]]
    assert client.get("/watchlist", headers=bob).json() == []
    assert client.delete(f"/watchlist/{item['id']}", headers=bob).status_code == 404
    assert client.delete(f"/watchlist/{item['id']}", headers=alice).status_code == 204
    assert client.get("/watchlist", headers=alice).json() == []


def test_read_only_paths_preserve_source_identity_with_another_adapter(client, monkeypatch):
    class AlternateDataProvider:
        provider_id = "alternate"

        async def search_instruments(self, query, *, exchange="NSE", segment="EQ"):
            assert query == "ABC"
            return [DiscoveredInstrument(
                instrument_key="ALT|ABC", trading_symbol="ABC", exchange=exchange,
                segment=segment, instrument_type="EQ",
            )]

        async def get_quote(self, instrument_token):
            assert instrument_token == "ALT|ABC"
            return Quote(
                instrument_token=instrument_token, timestamp=datetime.now(timezone.utc),
                last_price="100",
            )

        async def get_historical(self, instrument_token, start, end, interval):
            assert instrument_token == "ALT|ABC"
            assert interval == "1d"
            return [HistoricalBar(
                timestamp=start, open="100", high="102", low="99", close="101",
                volume=10,
            )]

        async def get_intraday(self, instrument_token, interval):
            assert instrument_token == "ALT|ABC"
            assert interval == "1d"
            return [HistoricalBar(
                timestamp=datetime.now(timezone.utc), open="101", high="103",
                low="100", close="102", volume=12,
            )]

    monkeypatch.setattr(
        "apps.api.main.get_market_data_provider", lambda: AlternateDataProvider()
    )
    headers = _headers(client, "other-source@example.com")
    assert client.get(
        "/market-data/instrument-search?query=ABC&provider_id=upstox", headers=headers
    ).status_code == 409
    search = client.get(
        "/market-data/instrument-search?query=ABC&provider_id=alternate", headers=headers
    )
    assert search.status_code == 200, search.text
    body = {
        "query": "ABC", "instrument_key": "ALT|ABC", "exchange": "NSE",
        "segment": "EQ", "provider_id": "alternate",
    }
    assert client.post(
        "/watchlist", json={**body, "provider_id": "upstox"}, headers=headers
    ).status_code == 409
    saved = client.post("/watchlist", json=body, headers=headers)
    assert saved.status_code == 201, saved.text
    assert saved.json()["provider"] == "alternate"

    for route in (
        "/market-data/quote?instrument_key=ALT%7CABC",
        "/market-data/historical?instrument_key=ALT%7CABC&start=2026-09-25T00%3A00%3A00Z"
        "&end=2026-09-26T00%3A00%3A00Z&interval=1d",
    ):
        assert client.get(f"{route}&provider_id=upstox", headers=headers).status_code == 409
        assert client.get(f"{route}&provider_id=alternate", headers=headers).status_code == 200
    intraday = client.get(
        "/market-data/intraday?instrument_key=ALT%7CABC&interval=1d"
        "&provider_id=alternate", headers=headers,
    )
    assert intraday.status_code == 200
    assert intraday.json()[0]["close"] == "102"
