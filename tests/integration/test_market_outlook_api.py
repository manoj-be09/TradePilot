from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from packages.market_data.contracts import DiscoveredInstrument, ExchangeSession, HistoricalBar

IST = ZoneInfo("Asia/Kolkata")


def test_outlook_requires_sign_in_and_preserves_provider_identity(client, monkeypatch):
    assert client.get("/analytics/market-outlook").status_code == 401
    auth = client.post(
        "/auth/register",
        json={"email": "outlook@example.com", "password": "a-long-test-password"},
    )
    token = auth.json()["access_token"]
    today = datetime.now(timezone.utc).astimezone(IST).date()
    expected = today - timedelta(days=1)

    class AlternateProvider:
        provider_id = "alternate"

        async def search_instruments(self, query, *, exchange, segment):
            assert (query, exchange, segment) == ("NIFTY 50", "NSE", "INDEX")
            return [DiscoveredInstrument(
                instrument_key="ALT|NIFTY", trading_symbol="NIFTY", name="Nifty 50",
                exchange="NSE", segment="INDEX", instrument_type="INDEX",
            )]

        async def get_equity_session(self, trading_day):
            if trading_day != expected:
                return None
            return ExchangeSession(
                exchange="NSE",
                start=datetime.combine(expected, time(9, 15), IST),
                end=datetime.combine(expected, time(15, 30), IST),
            )

        async def get_historical(self, instrument_key, start, end, interval):
            assert instrument_key == "ALT|NIFTY" and interval == "1d"
            return [HistoricalBar(
                timestamp=datetime.combine(expected - timedelta(days=129 - index), time(), IST),
                open=Decimal(20000 + index * 3 + index % 5),
                high=Decimal(20020 + index * 3 + index % 5),
                low=Decimal(19980 + index * 3 + index % 5),
                close=Decimal(20000 + index * 3 + index % 5),
                volume=100,
            ) for index in range(130)]

    monkeypatch.setattr(
        "apps.api.market_outlook.get_market_data_provider", lambda: AlternateProvider()
    )
    response = client.get(
        "/analytics/market-outlook", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["provider"] == "alternate"
    assert body["instrument_key"] == "ALT|NIFTY"
    assert body["session_date"] == expected.isoformat()
    assert body["relative_volatility_score"] is not None
    assert "forecast" not in body
