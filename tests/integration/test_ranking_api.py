from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from starlette.testclient import TestClient

from packages.database.models import StrategyDemotion, User
from packages.paper_trading.strategies import STRATEGY_REGISTRY
from tests.unit.test_nifty_ranking import _chain


def test_ranking_route_uses_account_lot_size_and_abstains_without_regime(
    client: TestClient, monkeypatch,
):
    auth = client.post(
        "/auth/register", json={"email": "ranking@example.com", "password": "a-long-test-password"},
    )
    headers = {"Authorization": f"Bearer {auth.json()['access_token']}"}
    chain = _chain(datetime.now(timezone.utc).replace(microsecond=0))

    class Provider:
        provider_id = "test"

        async def get_historical(self, *args):
            return []

        async def get_option_contracts(self, underlying="NIFTY", expiry=None):
            return [SimpleNamespace(expiry=chain.expiry, lot_size=65)]

    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: Provider())
    response = client.post(
        "/analytics/options/ranking", headers=headers,
        json={"chain": chain.model_dump(mode="json"), "reference_strike": "100"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "NO_TRADE"
    assert body["regime"] == "UNKNOWN"
    assert body["probability_of_profit"] is None
    assert len(body["candidates"]) == len(STRATEGY_REGISTRY.list())
    assert {item["lot_size"] for item in body["candidates"]} == {65}
    assert [item["stance"] for item in body["specialists"]] == [
        "BULLISH", "BEARISH", "RANGE", "VOLATILITY",
    ]


def test_ranking_route_blocks_a_paused_strategy_with_its_reason(
    client: TestClient, monkeypatch,
):
    auth = client.post(
        "/auth/register", json={"email": "paused@example.com", "password": "a-long-test-password"},
    )
    headers = {"Authorization": f"Bearer {auth.json()['access_token']}"}
    chain = _chain(datetime.now(timezone.utc).replace(microsecond=0))

    class Provider:
        provider_id = "test"

        async def get_historical(self, *args):
            return []

        async def get_option_contracts(self, underlying="NIFTY", expiry=None):
            return [SimpleNamespace(expiry=chain.expiry, lot_size=65)]

    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: Provider())
    with client.app.state.test_session_factory() as session:
        user = session.scalars(select(User)).one()
        session.add(StrategyDemotion(
            user_id=user.id, strategy_key="bull_put_spread", status="ACTIVE", reason="DRAWDOWN",
            details={"summary": "a test drawdown"}, demoted_at=datetime.now(timezone.utc),
        ))
        session.commit()
    body = client.post(
        "/analytics/options/ranking", headers=headers,
        json={"chain": chain.model_dump(mode="json"), "reference_strike": "100"},
    ).json()
    paused = next(item for item in body["candidates"] if item["strategy_key"] == "bull_put_spread")
    assert paused["status"] != "ELIGIBLE"
    assert any("a test drawdown" in reason for reason in paused["reasons"])
    assert body["selected_strategy_key"] != "bull_put_spread"


def test_ranking_route_requires_authentication(client: TestClient):
    response = client.post("/analytics/options/ranking", json={})
    assert response.status_code == 401
