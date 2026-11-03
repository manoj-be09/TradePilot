from datetime import date, datetime, timedelta, timezone

from packages.backtesting.published_strategies import Market, TradeResult
from packages.backtesting.rule_engine import ENGINE_RULES, run_engine


def test_rule_engine_trail_is_authenticated_and_lists_each_decision(client):
    assert client.get("/research/rule-engine").status_code == 401
    token = client.post(
        "/auth/register",
        json={"email": "engine@example.com", "password": "a-strong-test-password"},
    ).json()["access_token"]
    start = date(2026, 11, 2)
    with client.app.state.test_session_factory() as session:
        trade = TradeResult(start + timedelta(days=1), start + timedelta(days=2), 900.0, 80.0)
        run_engine(session, Market([], 0.0), datetime(2026, 11, 20, 11, 30, tzinfo=timezone.utc),
                   ENGINE_RULES, lambda rule, market: [trade])
        session.commit()
    body = client.get("/research/rule-engine",
                      headers={"Authorization": f"Bearer {token}"}).json()
    assert [row["stage"] for row in body["rules"]] == ["SHADOW", "SHADOW"]
    assert body["rules"][0]["check"]["trades"] == 1
    assert {event["event_type"] for event in body["events"]} == {"REGISTERED"}
    assert body["events"][0]["occurred_at"].endswith(("Z", "+00:00"))
    assert body["trials_tested"] == 12_500
