"""A paper entry checks India VIX from the recorder's fresh minute, and waits without one."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from test_paper_trading import FakeUpstoxProvider, _token

from packages.database.recorder_models import MarketSnapshot1m
from packages.recorder.universe import INDIA_VIX_KEY


def _enter(client, provider, headers):
    return client.post("/paper/trades", headers={**headers, "Idempotency-Key": str(uuid4())}, json={
        "strategy_key": "long_call", "expiry": provider.expiry.isoformat(),
        "reference_strike": "25000", "lots": 1,
        "entry_conditions": [
            {"metric": "india_vix", "operator": "GTE", "threshold": "15"},
            {"metric": "days_to_expiry", "operator": "GTE", "threshold": "0"},
        ],
    })


def test_vix_condition_waits_without_a_recorded_minute_then_passes_with_one(
    client, monkeypatch
):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)

    refused = _enter(client, provider, headers)
    assert refused.status_code == 409
    assert refused.json()["detail"]["conditions"][0]["passed"] is None

    now = datetime.now(timezone.utc)
    with client.app.state.test_session_factory() as session:
        session.add(MarketSnapshot1m(
            instrument_key=INDIA_VIX_KEY, bucket_start=now.replace(second=0, microsecond=0),
            trading_day=(now + timedelta(hours=5, minutes=30)).date(), received_at=now,
            ltp_close=Decimal("17.4"), prev_close=Decimal("18.4"), message_count=1))
        session.commit()
    opened = _enter(client, provider, headers)
    assert opened.status_code == 201, opened.text
    vix, days = opened.json()["entry_rule_snapshot"]["results"]
    assert (vix["actual"], vix["passed"]) == ("17.40", True)
    assert days["passed"] is True


def test_an_iv_against_realized_volatility_rule_reads_daily_candles_only_when_used(
    client, monkeypatch
):
    from types import SimpleNamespace

    from packages.paper_trading import market_context

    market_context._rv_cache.clear()
    provider = FakeUpstoxProvider()
    calls = []

    async def daily(key, start, end, interval):
        calls.append(interval)
        return [SimpleNamespace(timestamp=end - timedelta(days=d), close=Decimal(25000))
                for d in range(15, 0, -1)]

    provider.get_historical = daily
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _token(client)
    plain = client.post("/paper/trades", headers={**headers, "Idempotency-Key": str(uuid4())},
                        json={"strategy_key": "long_call", "expiry": provider.expiry.isoformat(),
                              "reference_strike": "25000", "lots": 1})
    assert plain.status_code == 201 and calls == []
    refused = client.post("/paper/trades", headers={**headers, "Idempotency-Key": str(uuid4())},
                          json={"strategy_key": "long_put", "expiry": provider.expiry.isoformat(),
                                "reference_strike": "25000", "lots": 1, "entry_conditions": [
                                    {"metric": "realized_vol_10d_pct", "operator": "GT",
                                     "threshold": "1"}]})
    assert refused.status_code == 409  # flat closes: 0% realized volatility
    result = refused.json()["detail"]["conditions"][0]
    assert (result["actual"], result["passed"]) == ("0.00", False)
    assert calls == ["1d"]
