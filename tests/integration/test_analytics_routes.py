from datetime import date, datetime, timedelta, timezone

from starlette.testclient import TestClient


def test_phase_four_analytics_routes_are_documented(client: TestClient):
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "get" in paths["/analytics/options"]
    assert "post" in paths["/analytics/options/summary"]
    assert "get" in paths["/analytics/indicators"]
    assert "get" in paths["/analytics/regime"]
    assert "post" in paths["/analytics/greeks"]
    assert "post" in paths["/analytics/payoff"]


def test_authenticated_analytics_accept_chain_and_return_payoffs(client: TestClient, monkeypatch):
    auth_response = client.post(
        "/auth/register",
        json={"email": "analytics@example.com", "password": "a-long-test-password"},
    )
    assert auth_response.status_code == 201
    token = auth_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    timestamp = datetime.now(timezone.utc).isoformat()
    expiry = (date.today() + timedelta(days=7)).isoformat()
    chain = {
        "underlying": "NIFTY",
        "underlying_spot": "25000",
        "expiry": expiry,
        "as_of": timestamp,
        "timestamp_basis": "api_response_received",
        "contracts": [
            {
                "instrument_token": "NSE_FO|NIFTY_CALL",
                "expiry": expiry,
                "strike": "25000",
                "option_type": "CE",
                "quote": {
                    "instrument_token": "NSE_FO|NIFTY_CALL",
                    "timestamp": timestamp,
                    "last_price": "100",
                    "bid": "99",
                    "ask": "101",
                    "volume": 100,
                },
                "open_interest": 1000,
                "open_interest_change": 10,
                "implied_volatility": "20",
                "greeks": {"delta": "0.5"},
            },
            {
                "instrument_token": "NSE_FO|NIFTY_PUT",
                "expiry": expiry,
                "strike": "25000",
                "option_type": "PE",
                "quote": {
                    "instrument_token": "NSE_FO|NIFTY_PUT",
                    "timestamp": timestamp,
                    "last_price": "100",
                    "bid": "99",
                    "ask": "101",
                    "volume": 200,
                },
                "open_interest": 2000,
                "open_interest_change": 20,
                "implied_volatility": "22",
                "greeks": {"delta": "-0.5"},
            },
        ],
    }
    summary_response = client.post("/analytics/options/summary", json=chain, headers=headers)

    assert summary_response.status_code == 200
    assert summary_response.json()["put_call_ratio_oi"] == "2"
    assert summary_response.json()["atm_iv_skew_put_minus_call_points"] == "2"

    class NoIntradayHistory:
        async def get_historical(self, *args):
            return []

    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: NoIntradayHistory())
    fit_response = client.post(
        "/analytics/options/market-fit",
        headers=headers,
        json={"chain": chain, "reference_strike": "25000"},
    )
    assert fit_response.status_code == 200
    assert fit_response.json()["action"] == "NO_TRADE"
    assert fit_response.json()["probability_of_profit"] is None
    assert {item["strategy_key"] for item in fit_response.json()["candidates"]} == {
        "bull_call_spread", "bear_put_spread", "iron_condor"
    }

    payoff_response = client.post(
        "/analytics/payoff",
        headers=headers,
        json={
            "underlying": "NIFTY",
            "multiplier": "75",
            "price_min": "0",
            "price_max": "200",
            "grid_points": 3,
            "legs": [{"option_type": "CE", "strike": "100", "quantity": 1, "entry_premium": "5"}],
        },
    )

    assert payoff_response.status_code == 200
    assert payoff_response.json()["breakeven_prices"] == ["105"]
    assert payoff_response.json()["maximum_profit_unbounded"] is True
    scenario = payoff_response.json()["transaction_cost_scenario"]
    assert scenario["model_id"] == "upstox-public-nfo-options-2026-10-scenario-v1"
    assert scenario["complete_broker_costs"] is False
    assert scenario["round_trip_scenario_charge"] == "48.07"
