from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from packages.database.models import User, WatchlistItem
from packages.market_data.contracts import DiscoveredInstrument, HistoricalBar


def test_shadow_fit_is_authenticated_read_only_and_provider_neutral(client, monkeypatch):
    registered = client.post(
        "/auth/register",
        json={"email": "shadow@example.com", "password": "a-long-test-password"},
    )
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    now = datetime.now(timezone.utc)
    last = now - timedelta(days=1)
    stock = []
    nifty = []
    for index in range(760):
        stamp = last - timedelta(days=759 - index)
        close = Decimal(100 + index // 3 + index % 7)
        stock.append(HistoricalBar(
            timestamp=stamp, open=close, high=close + 2, low=close - 2,
            close=close, volume=10000,
        ))
        index_close = Decimal(20000 + index * 3 + index % 7)
        nifty.append(HistoricalBar(
            timestamp=stamp, open=index_close, high=index_close + 10,
            low=index_close - 10, close=index_close, volume=10000,
        ))

    class AlternateProvider:
        provider_id = "alternate"

        async def get_historical(self, instrument_key, start, end, interval):
            assert interval == "1d"
            return nifty if instrument_key == "ALT|NIFTY" else stock

        async def get_quote(self, instrument_key):
            raise RuntimeError("closed market")

        async def get_equity_session(self, trading_day):
            return None

    provider = AlternateProvider()
    monkeypatch.setattr("apps.api.equity_routes.get_market_data_provider", lambda: provider)

    async def benchmark(_provider):
        return DiscoveredInstrument(
            instrument_key="ALT|NIFTY", trading_symbol="NIFTY", name="Nifty 50",
            exchange="NSE", segment="INDEX", instrument_type="INDEX",
        )

    async def session(_provider, _now):
        return last.astimezone(timezone(timedelta(hours=5, minutes=30))).date()

    monkeypatch.setattr("apps.api.equity_routes.nifty_benchmark", benchmark)
    monkeypatch.setattr("apps.api.equity_routes.latest_completed_session", session)
    factory = client.app.state.test_session_factory
    with factory() as db:
        user = db.scalar(select(User).where(User.email == "shadow@example.com"))
        item = WatchlistItem(
            user_id=user.id, provider="alternate", instrument_key="ALT|TEST",
            trading_symbol="TEST", exchange="NSE", segment="NSE_EQ", instrument_type="EQ",
        )
        db.add(item)
        db.commit()
        item_id = str(item.id)
    body = {"watchlist_id": item_id, "budget": "10000", "risk_budget": "200"}
    assert client.post("/equity/strategy-fit-shadow", json=body).status_code == 401
    before = client.post("/equity/strategies", json=body, headers=headers)
    assert before.status_code == 200, before.text
    assert len(before.json()["top_three"]) == 3
    assert set(before.json()["top_three"]).issubset({
        row["key"] for row in before.json()["strategies"]
    })
    assert before.json()["market_context"]["basis"].startswith("Selected stock")
    account_before = client.get("/equity/deployments", headers=headers).json()
    response = client.post("/equity/strategy-fit-shadow", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["status"] == "ready", result
    assert result["provider"] == "alternate"
    assert result["symbol"] == "TEST"
    assert len(result["rows"]) == 10
    assert result["ranking_changed"] is False
    assert result["out_of_sample_validated"] is False
    assert client.get("/equity/deployments", headers=headers).json() == account_before
    after = client.post("/equity/strategies", json=body, headers=headers)
    assert [row["key"] for row in after.json()["strategies"]] == [
        row["key"] for row in before.json()["strategies"]
    ]
