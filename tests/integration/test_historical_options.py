from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import httpx

from packages.market_data.contracts import ExpiredOptionContract, HistoricalBar
from packages.market_data.upstox import UpstoxMarketDataProvider


def _register(client, email: str = "history@example.com") -> dict[str, str]:
    response = client.post(
        "/auth/register", json={"email": email, "password": "a-long-test-password"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_expired_option_market_data_routes_require_auth_and_return_real_provider_data(
    client, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/expired-instruments/expiries":
            return httpx.Response(200, json={"status": "success", "data": ["2025-04-17"]})
        if request.url.path == "/v2/expired-instruments/option/contract":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": [
                        {
                            "instrument_key": "NSE_FO|47982|17-04-2025",
                            "trading_symbol": "NIFTY 20400 CE 17 APR 25",
                            "expiry": "2025-04-17",
                            "strike_price": 20400,
                            "instrument_type": "CE",
                            "lot_size": 75,
                            "tick_size": 0.05,
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"candles": [["2025-04-17T00:00:00+05:30", 100, 110, 90, 105, 300, 5000]]},
            },
        )

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    assert client.get("/market-data/expired-option-expiries").status_code == 401
    headers = _register(client)

    expiries = client.get("/market-data/expired-option-expiries", headers=headers)
    assert expiries.status_code == 200
    assert expiries.json() == ["2025-04-17"]
    contracts = client.get(
        "/market-data/expired-option-contracts?expiry_date=2025-04-17", headers=headers
    )
    assert contracts.status_code == 200
    assert contracts.json()[0]["instrument_key"] == "NSE_FO|47982|17-04-2025"
    bars = client.get(
        "/market-data/expired-option-candles",
        headers=headers,
        params={
            "instrument_key": "NSE_FO|47982|17-04-2025",
            "start": "2025-04-17",
            "end": "2025-04-17",
            "interval": "1d",
        },
    )
    assert bars.status_code == 200
    assert bars.json()[0]["open_interest"] == 5000


def test_expired_option_plus_plan_requirement_maps_to_403(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(403, json={"errors": [{"errorCode": "UDAPI1149"}]})

    provider = UpstoxMarketDataProvider("test-token", transport=httpx.MockTransport(handler))
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    response = client.get("/market-data/expired-option-expiries", headers=_register(client))

    assert response.status_code == 403
    assert "Upstox Plus" in response.json()["detail"]


def test_expired_option_candle_route_rejects_reversed_dates_before_provider_call(
    client, monkeypatch
):
    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected upstream request: {request.url}")

    provider = UpstoxMarketDataProvider(
        "test-token", transport=httpx.MockTransport(unexpected_request)
    )
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    response = client.get(
        "/market-data/expired-option-candles",
        headers=_register(client),
        params={
            "instrument_key": "NSE_FO|47982|17-04-2025",
            "start": "2025-04-18",
            "end": "2025-04-17",
        },
    )

    assert response.status_code == 422


def test_authenticated_backtest_route_returns_auditable_historical_replay(client, monkeypatch):
    expiry = date.today() - timedelta(days=7)
    entry = expiry - timedelta(days=5)

    class ReplayProvider:
        provider_id = "historical-fixture"
        contracts = [
            ExpiredOptionContract(
                instrument_key=f"NSE_FO|{strike}{side}|{expiry:%d-%m-%Y}",
                trading_symbol=f"NIFTY {strike} {side}",
                expiry=expiry,
                strike=Decimal(strike),
                option_type=side,
                lot_size=25,
                tick_size=Decimal("0.05"),
            )
            for strike in (24900, 24950, 25000, 25050, 25100)
            for side in ("CE", "PE")
        ]

        async def get_historical(self, instrument_key, start, end, interval):
            del start, end
            assert instrument_key == "NSE_INDEX|Nifty 50"
            assert interval == "1d"
            return [
                HistoricalBar(
                    timestamp=datetime.combine(entry - timedelta(days=1), time.min, timezone.utc),
                    open=25000,
                    high=25100,
                    low=24900,
                    close=25012,
                    volume=1000,
                )
            ]

        async def get_expired_option_contracts(self, underlying, requested_expiry):
            assert underlying == "NIFTY"
            assert requested_expiry == expiry
            return self.contracts

        async def get_expired_historical(self, instrument_key, start, end, interval):
            del start, end
            assert interval == "1d"
            item = next(c for c in self.contracts if c.instrument_key == instrument_key)
            entry_price, exit_price = (
                (Decimal(100), Decimal(130))
                if item.strike == Decimal(25000)
                else (Decimal(80), Decimal(60))
            )
            return [
                HistoricalBar(
                    timestamp=datetime.combine(entry, time.min, timezone.utc),
                    open=entry_price,
                    high=entry_price,
                    low=entry_price,
                    close=entry_price,
                    volume=100,
                ),
                HistoricalBar(
                    timestamp=datetime.combine(expiry, time.min, timezone.utc),
                    open=exit_price,
                    high=exit_price,
                    low=exit_price,
                    close=exit_price,
                    volume=100,
                ),
            ]

    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: ReplayProvider())
    headers = _register(client)
    assert client.get("/backtests/options/runs").status_code == 401
    response = client.post(
        "/backtests/options/run",
        headers=headers,
        json={
            "strategy_key": "bull_call_spread",
            "expiry": expiry.isoformat(),
            "entry_date": entry.isoformat(),
            "exit_date": expiry.isoformat(),
            "lots": 1,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["gross_pnl"] == "1250.00"
    assert response.json()["provider"] == "historical-fixture"
    assert response.json()["maximum_loss_at_entry"] == "500.00"
    assert response.json()["legs"][0]["entry_price"] == "100"

    history = client.get("/backtests/options/runs", headers=headers)
    assert history.status_code == 200
    assert len(history.json()) == 1
    assert history.json()[0]["result"]["run_id"] == response.json()["run_id"]
    assert history.json()[0]["result"]["gross_pnl"] == "1250.00"

    other_user_history = client.get(
        "/backtests/options/runs", headers=_register(client, "other-history@example.com")
    )
    assert other_user_history.status_code == 200
    assert other_user_history.json() == []


def test_scheduled_backtest_route_saves_user_scoped_history(client, monkeypatch):
    from tests.unit.test_scheduled_backtesting import ScheduledProvider

    monkeypatch.setattr("apps.api.main.get_market_data_provider", ScheduledProvider)
    headers = _register(client, "schedule@example.com")
    body = {
        "strategy_key": "bull_call_spread",
        "start_date": "2025-04-10",
        "end_date": "2025-04-17",
        "entry_sessions_before_expiry": 1,
        "lots": 1,
        "fee_per_order": "20",
        "slippage_per_unit_per_side": "0.50",
    }
    assert client.post("/backtests/options/scheduled-run", json=body).status_code == 401
    response = client.post("/backtests/options/scheduled-run", headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["trade_count"] == 2
    assert response.json()["net_pnl"] == "-10.00"
    assert len(response.json()["observed_input_digest"]) == 64

    saved = client.get("/backtests/options/scheduled-runs", headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()[0]["result"]["run_id"] == response.json()["run_id"]
    assert client.get("/backtests/options/runs", headers=headers).json() == []
    other = _register(client, "other-schedule@example.com")
    assert client.get("/backtests/options/scheduled-runs", headers=other).json() == []
