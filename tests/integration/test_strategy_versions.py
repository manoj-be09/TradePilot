from uuid import uuid4

from packages.market_data.index_catalog import OptionIndex, resolve_option_index
from tests.integration.test_paper_trading import FakeUpstoxProvider


def _headers(client, email: str) -> dict[str, str]:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": "a-strong-test-password"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _definition(**changes):
    definition = {
        "name": "Morning call",
        "strategy_key": "long_call",
        "lots": 1,
        "entry_conditions": [],
        "stop_loss_amount": "150",
        "target_profit_amount": "250",
        "trailing_stop_amount": None,
    }
    return {**definition, **changes}


def test_explicit_nifty_retains_legacy_definition_hash(client):
    headers = _headers(client, "nifty-version-hash@example.com")
    legacy = client.post("/strategies/versions", headers=headers, json=_definition()).json()
    explicit = client.post("/strategies/versions", headers=headers, json=_definition(
        name="Explicit Nifty", underlying="NIFTY",
    ))
    assert explicit.status_code == 201, explicit.text
    assert explicit.json()["definition_hash"] == legacy["definition_hash"]


def test_index_identity_is_pinned_and_cannot_use_legacy_execution(client, monkeypatch):
    headers = _headers(client, "sensex-version@example.com")
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    index = OptionIndex(symbol="SENSEX", instrument_key="BSE_INDEX|SENSEX",
                        name="SENSEX", exchange="BSE", derivatives_exchange="BFO")

    async def resolve(underlying):
        if underlying == "NIFTY":
            return await resolve_option_index(underlying)
        assert underlying == "SENSEX"
        return index

    monkeypatch.setattr("apps.api.main.resolve_option_index", resolve)
    created = client.post("/strategies/versions", headers=headers, json=_definition(
        underlying="SENSEX", stop_loss_amount=None, target_profit_amount=None,
    ))
    assert created.status_code == 201, created.text
    version = created.json()
    assert version["definition"]["option_index"] == index.model_dump()
    entry = {
        "strategy_version_id": version["id"], "strategy_key": "long_call",
        "expiry": provider.expiry.isoformat(), "reference_strike": "25000", "lots": 1,
    }
    opened = client.post("/paper/trades", headers={
        **headers, "Idempotency-Key": str(uuid4()),
    }, json=entry)
    assert opened.status_code == 409, opened.text
    replay = client.post("/backtests/options/run", headers=headers, json={
        "strategy_version_id": version["id"], "strategy_key": "long_call",
        "expiry": "2026-10-01", "entry_date": "2026-09-29",
        "exit_date": "2026-09-30", "lots": 1,
    })
    assert replay.status_code == 409, replay.text
    assert "Index-specific strategy replay" in replay.json()["detail"]
    assert client.get("/paper/orders", headers=headers).json() == []


def test_versions_are_append_only_user_scoped_and_require_latest_base(client):
    first_user = _headers(client, "version-one@example.com")
    second_user = _headers(client, "version-two@example.com")
    assert client.get("/strategies/versions").status_code == 401

    created = client.post("/strategies/versions", headers=first_user, json=_definition())
    assert created.status_code == 201, created.text
    v1 = created.json()
    assert v1["version_number"] == 1
    assert v1["definition"]["template_key"] == "long_call"
    assert v1["definition"]["template_version"] == "1.0.0"
    assert v1["definition"]["template_legs"] == [
        {"option_type": "CE", "side": "BUY", "strike_offset": 0}
    ]
    assert len(v1["definition_hash"]) == 64
    assert client.get(f"/strategies/versions/{v1['id']}", headers=second_user).status_code == 404
    assert client.get("/strategies/versions", headers=second_user).json() == []

    duplicate_name = client.post("/strategies/versions", headers=first_user, json=_definition())
    assert duplicate_name.status_code == 409
    unchanged = client.post(
        "/strategies/versions",
        headers=first_user,
        json=_definition(base_version_id=v1["id"]),
    )
    assert unchanged.status_code == 409

    created_again = client.post(
        "/strategies/versions",
        headers=first_user,
        json=_definition(lots=2, base_version_id=v1["id"]),
    )
    assert created_again.status_code == 201, created_again.text
    v2 = created_again.json()
    assert v2["version_number"] == 2
    assert v2["definition_hash"] != v1["definition_hash"]
    assert client.get(f"/strategies/versions/{v1['id']}", headers=first_user).json() == v1
    assert client.post(
        "/strategies/versions",
        headers=first_user,
        json=_definition(lots=3, base_version_id=v1["id"]),
    ).status_code == 409
    assert len(client.get("/strategies/versions", headers=first_user).json()) == 2


def test_paper_entry_pins_matching_version_and_rejects_edits(client, monkeypatch):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = _headers(client, "version-trader@example.com")
    version = client.post(
        "/strategies/versions", headers=headers, json=_definition()
    ).json()
    entry = {
        "strategy_version_id": version["id"],
        "strategy_key": "long_call",
        "expiry": provider.expiry.isoformat(),
        "reference_strike": "25000",
        "lots": 1,
        "entry_conditions": [],
        "stop_loss_amount": "150",
        "target_profit_amount": "250",
        "trailing_stop_amount": None,
    }
    mismatch = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={**entry, "lots": 2},
    )
    assert mismatch.status_code == 409
    unknown = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={**entry, "strategy_version_id": str(uuid4())},
    )
    assert unknown.status_code == 404
    opened = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json=entry,
    )
    assert opened.status_code == 201, opened.text
    snapshot = opened.json()["entry_rule_snapshot"]
    assert snapshot["strategy_version_id"] == version["id"]
    assert snapshot["strategy_definition_hash"] == version["definition_hash"]

def test_single_replay_records_only_compatible_saved_version(client, monkeypatch):
    from datetime import date, timedelta
    from decimal import Decimal

    from packages.backtesting.contracts import OptionsBacktestResult

    headers = _headers(client, "version-replay@example.com")
    supported = client.post(
        "/strategies/versions",
        headers=headers,
        json=_definition(stop_loss_amount=None, target_profit_amount=None),
    ).json()
    unsupported = client.post(
        "/strategies/versions",
        headers=headers,
        json=_definition(name="Filtered call"),
    ).json()
    other_headers = _headers(client, "version-replay-other@example.com")
    expiry = date.today() - timedelta(days=7)
    entry = expiry - timedelta(days=3)
    calls = []

    async def fake_replay(provider, strategy, *, expiry, entry_date, exit_date, lots,
                          fee_per_order, slippage_per_unit_per_side):
        calls.append(strategy.key)
        return OptionsBacktestResult(
            run_id=uuid4(), provider="upstox", mode="historical_single_expiry_replay",
            strategy_key=strategy.key, strategy_name=strategy.name,
            expiry=expiry, entry_date=entry_date, exit_date=exit_date,
            entry_reference_spot=Decimal("25000"), reference_strike=Decimal("25000"),
            lot_size=25, lots=lots, gross_pnl=Decimal("100"),
            maximum_loss_at_entry=Decimal("2500"), return_on_risk_pct=Decimal("4"),
            legs=[], assumptions=[], warnings=[],
        )

    monkeypatch.setattr("apps.api.main.run_single_expiry_replay", fake_replay)
    payload = {
        "strategy_version_id": supported["id"], "strategy_key": "long_call",
        "expiry": expiry.isoformat(), "entry_date": entry.isoformat(),
        "exit_date": expiry.isoformat(), "lots": 1,
    }
    assert client.post(
        "/backtests/options/run", headers=other_headers, json=payload
    ).status_code == 404
    assert client.post(
        "/backtests/options/run", headers=headers, json={**payload, "lots": 2}
    ).status_code == 409
    assert client.post(
        "/backtests/options/run", headers=headers,
        json={**payload, "strategy_version_id": unsupported["id"]},
    ).status_code == 422
    assert calls == []
    accepted = client.post("/backtests/options/run", headers=headers, json=payload)
    assert accepted.status_code == 200, accepted.text
    assert calls == ["long_call"]
    assert accepted.json()["strategy_version_id"] == supported["id"]
    assert accepted.json()["strategy_definition_hash"] == supported["definition_hash"]
    assert accepted.json()["net_pnl"] is None
    saved = client.get("/backtests/options/runs", headers=headers).json()
    assert saved[0]["result"]["strategy_version_id"] == supported["id"]


def test_scheduled_replay_records_compatible_saved_version(client, monkeypatch):
    from datetime import date, timedelta
    from decimal import Decimal

    from packages.backtesting.contracts import ScheduledOptionsBacktestResult

    headers = _headers(client, "scheduled-version@example.com")
    version = client.post(
        "/strategies/versions",
        headers=headers,
        json=_definition(stop_loss_amount=None, target_profit_amount=None),
    ).json()
    expiry = date.today() - timedelta(days=7)

    async def fake_replay(provider, strategy, *, start_date, end_date,
                          entry_sessions_before_expiry, lots, fee_per_order,
                          slippage_per_unit_per_side):
        return ScheduledOptionsBacktestResult(
            run_id=uuid4(), provider="upstox", mode="historical_scheduled_expiry_replay",
            strategy_key=strategy.key, strategy_name=strategy.name,
            strategy_version=strategy.version, start_date=start_date,
            end_date=end_date, entry_sessions_before_expiry=entry_sessions_before_expiry,
            lots=lots, fee_per_order=fee_per_order,
            slippage_per_unit_per_side=slippage_per_unit_per_side,
            trade_count=0, gross_pnl=Decimal("0"), estimated_cost=Decimal("0"),
            net_pnl=Decimal("0"), max_closed_trade_drawdown=Decimal("0"),
            winning_trades=0, observed_input_digest="a" * 64, trades=[],
            assumptions=[], warnings=[],
        )

    monkeypatch.setattr("apps.api.main.run_scheduled_expiry_replay", fake_replay)
    result = client.post(
        "/backtests/options/scheduled-run", headers=headers,
        json={
            "strategy_version_id": version["id"], "strategy_key": "long_call",
            "start_date": expiry.isoformat(), "end_date": expiry.isoformat(),
            "lots": 1,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["strategy_version_id"] == version["id"]
    assert result.json()["strategy_definition_hash"] == version["definition_hash"]
