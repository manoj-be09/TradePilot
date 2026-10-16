import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select, update
from test_paper_trading import FakeUpstoxProvider, _token

from apps.api.services.paper_monitor import monitor_exits
from apps.api.settings import get_settings
from apps.paper_worker.main import run_cycle
from packages.database.models import AuditEvent, PaperOrder, PaperTrade, WorkerLease
from packages.market_data.contracts import ExchangeSession, UnconfiguredMarketDataProvider
from packages.paper_trading.official_index_close import OfficialIndexClose, archive_url
from packages.paper_trading.session import exchange_today
from packages.paper_trading.transactions import lock_account
from packages.paper_trading.worker_lease import WORKER_NAME, acquire, assert_owned, finish


def setup_position(client, monkeypatch, **rules):
    provider = FakeUpstoxProvider()
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = {**_token(client), "Idempotency-Key": "worker-test-entry"}
    payload = {
        "strategy_key": "long_call",
        "expiry": provider.expiry.isoformat(),
        "reference_strike": "25000",
        "lots": 1,
        **rules,
    }
    opened = client.post("/paper/trades", headers=headers, json=payload)
    assert opened.status_code == 201, opened.text
    factory = client.app.state.test_session_factory
    return provider, headers, payload, opened.json(), factory


def test_worker_closes_without_browser_or_jwt_and_restart_does_not_repeat(client, monkeypatch):
    provider, headers, _, trade, factory = setup_position(
        client,
        monkeypatch,
        stop_loss_amount="100",
    )
    provider.price_adjustment = Decimal(-10)
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        assert saved.status == "CLOSED"
        assert saved.exit_reason == "STOP_LOSS"
        orders = list(session.scalars(select(PaperOrder)))
        assert len(orders) == 2
        assert all(order.fill_snapshot["modeled_costs"]["model_id"] ==
                   "option-premium-turnover-illustrative-v1" for order in orders)
    assert client.get("/paper/worker").status_code == 401
    assert client.get("/paper/worker", headers=headers).json()["status"] == "HEALTHY"


def test_unsupported_index_never_uses_nifty_quotes_for_exit(client, monkeypatch):
    provider, headers, _, trade, factory = setup_position(
        client, monkeypatch, stop_loss_amount="100",
    )
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        saved.underlying = "SENSEX"
        session.commit()

    async def unexpected_chain(*args):
        raise AssertionError("Unsupported index must not fetch a NIFTY chain")

    provider.get_option_chain = unexpected_chain
    result = client.post("/paper/monitor", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["evaluated_count"] == 0
    assert result.json()["closed_trade_ids"] == []
    assert any("Non-NIFTY" in warning for warning in result.json()["warnings"])
    assert client.post("/paper/monitor", headers=headers).status_code == 200
    marked = client.get("/paper/trades", headers=headers)
    assert marked.status_code == 200, marked.text
    item = marked.json()["trades"][0]
    assert item["unrealized_pnl"] is None
    assert "Index-specific valuation" in item["valuation_warning"]
    closed = client.post(f"/paper/trades/{trade['id']}/close", headers=headers)
    assert closed.status_code == 409, closed.text
    assert "Index-specific paper close" in closed.json()["detail"]
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"
        assert len(list(session.scalars(select(PaperOrder)))) == 1
        checks = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked",
        )))
        assert len(checks) == 1
        assert checks[0].details["snapshot"]["reason_code"] == "UNSUPPORTED_INDEX"


def _expiry_day_position(client, monkeypatch):
    provider, headers, payload, trade, factory = setup_position(client, monkeypatch)
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        saved.expiry = exchange_today()
        session.commit()

    async def closing_session(_trading_day):
        now = datetime.now(timezone.utc)
        return ExchangeSession("NFO", now - timedelta(hours=6), now + timedelta(minutes=10))

    monkeypatch.setattr(provider, "get_nfo_session", closing_session)
    return provider, headers, payload, trade, factory


@pytest.mark.parametrize("exit_mode", ["manual", "worker"])
def test_pinned_bse_manual_close_requires_bfo_session(client, monkeypatch, exit_mode):
    # Disposable synthetic fixture; this is not a live BSE entry or expiry acceptance.
    provider, headers, payload, trade, factory = setup_position(client, monkeypatch)
    second = client.post("/paper/trades", headers={
        **headers, "Idempotency-Key": "same-expiry-nifty-peer",
    }, json=payload)
    assert second.status_code == 201, second.text
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        saved.underlying = "SENSEX"
        saved.entry_rule_snapshot = {**saved.entry_rule_snapshot, "option_index": {
            "symbol": "SENSEX", "instrument_key": "BSE_INDEX|SENSEX",
            "name": "SENSEX", "exchange": "BSE", "derivatives_exchange": "BFO",
        }}
        saved.legs = {**saved.legs, "items": [{
            **leg, "instrument_key": leg["instrument_key"].replace("NSE_FO|", "BSE_FO|"),
        } for leg in saved.legs["items"]]}
        session.commit()
    provider.contracts += [contract.model_copy(update={
        "instrument_key": contract.instrument_key.replace("NSE_FO|", "BSE_FO|"),
    }) for contract in provider.contracts]
    venue = "NFO"
    wrong_index = True
    requested = []

    async def selected_session(underlying, day):
        assert underlying == "SENSEX"
        return ExchangeSession(venue, provider.session_open, provider.session_close)

    original_chain = provider.get_option_chain

    async def selected_chain(underlying, expiry):
        requested.append(underlying)
        chain = await original_chain(underlying, expiry)
        prefix = "NSE_FO|" if underlying == "NIFTY" else "BSE_FO|"
        chain.contracts = [item for item in chain.contracts
                           if item.instrument_token.startswith(prefix)]
        if wrong_index and underlying == "SENSEX":
            chain.underlying = "BANKEX"
        return chain

    monkeypatch.setattr(provider, "get_index_option_session", selected_session)
    monkeypatch.setattr(provider, "get_option_chain", selected_chain)
    url = f"/paper/trades/{trade['id']}/close"
    rejected = client.post(url, headers=headers)
    assert rejected.status_code == 409, rejected.text
    assert requested == []
    skipped_bfo = client.post("/paper/monitor", headers=headers)
    assert skipped_bfo.status_code == 200, skipped_bfo.text
    assert skipped_bfo.json()["evaluated_count"] == 1
    assert skipped_bfo.json()["closed_trade_ids"] == []
    assert any("BFO scheduled session" in warning for warning in skipped_bfo.json()["warnings"])
    assert requested == ["NIFTY"]
    requested.clear()
    rows = client.get("/paper/trades", headers=headers).json()["trades"]
    unavailable_mark = next(item for item in rows if item["id"] == trade["id"])
    assert unavailable_mark["unrealized_pnl"] is None
    assert "BFO scheduled session" in unavailable_mark["valuation_warning"]
    assert requested == ["NIFTY"]
    requested.clear()
    venue = "BFO"
    rejected_chain = client.post(url, headers=headers)
    assert rejected_chain.status_code == 409, rejected_chain.text
    wrong_index = False
    marked = client.get("/paper/trades", headers=headers)
    assert marked.status_code == 200, marked.text
    assert all(item["unrealized_pnl"] is not None for item in marked.json()["trades"])
    requested.clear()
    if exit_mode == "manual":
        closed = client.post(url, headers=headers)
        assert closed.status_code == 200, closed.text
        assert requested == ["SENSEX"]
        assert closed.json()["status"] == "CLOSED"
    else:
        with factory() as session:
            saved = session.get(PaperTrade, UUID(trade["id"]))
            saved.target_profit_amount = Decimal("1")
            session.commit()
        provider.price_adjustment = Decimal("10")
        monitored = client.post("/paper/monitor", headers=headers)
        assert monitored.status_code == 200, monitored.text
        assert monitored.json()["closed_trade_ids"] == [trade["id"]]
        assert requested == ["NIFTY", "SENSEX"]
        assert client.post("/paper/monitor", headers=headers).json()["closed_trade_ids"] == []
    assert client.post(url, headers=headers).status_code == 409
    assert client.get("/paper/account/ledger-reconciliation", headers=headers).json()[
        "status"
    ] == "MATCHED"


def test_worker_square_off_expiry_position_once_with_verified_quote(client, monkeypatch):
    provider, headers, _, trade, factory = _expiry_day_position(client, monkeypatch)
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        assert saved.status == "CLOSED"
        assert saved.exit_reason == "EXPIRY_SQUARE_OFF"
        orders = list(session.scalars(select(PaperOrder)))
        assert len(orders) == 2
        assert orders[-1].fill_snapshot["exit_reason"] == "EXPIRY_SQUARE_OFF"
    report = client.get(
        f"/paper/trades/{trade['id']}/exit-reconciliation", headers=headers
    )
    assert report.json()["status"] == "MATCHED"
    assert report.json()["last_decision"] == "EXPIRY_SQUARE_OFF"


@pytest.mark.parametrize("close_method", ["manual", "stop"])
def test_explicit_expiry_test_holds_at_cutoff_but_retains_manual_close(
    client, monkeypatch, close_method,
):
    provider, headers, _, trade, factory = _expiry_day_position(client, monkeypatch)
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        saved.entry_rule_snapshot = saved.entry_rule_snapshot | {
            "testing_only": True,
            "exit_policy": {"version": "nifty-option-expiry-test-v1",
                            "square_off_minutes_before_close": 20},
        }
        session.commit()
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"
        assert len(list(session.scalars(select(PaperOrder)))) == 1
    if close_method == "manual":
        closed = client.post(f"/paper/trades/{trade['id']}/close", headers=headers)
        assert closed.status_code == 200
        assert closed.json()["status"] == "CLOSED"
    else:
        # The hold policy only disables expiry square-off, never an existing P&L stop.
        with factory() as session:
            saved = session.get(PaperTrade, UUID(trade["id"]))
            saved.stop_loss_amount = Decimal("1")
            session.commit()
        provider.price_adjustment = Decimal("-10")
        assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
        with factory() as session:
            saved = session.get(PaperTrade, UUID(trade["id"]))
            assert saved.status == "CLOSED"
            assert saved.exit_reason == "STOP_LOSS"
    assert client.get("/paper/account/ledger-reconciliation", headers=headers).json()[
        "status"
    ] == "MATCHED"


def test_unlabeled_expiry_hold_policy_cannot_bypass_square_off(client, monkeypatch):
    provider, _, _, trade, factory = _expiry_day_position(client, monkeypatch)
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        saved.entry_rule_snapshot = saved.entry_rule_snapshot | {
            "exit_policy": {"version": "nifty-option-expiry-test-v1",
                            "square_off_minutes_before_close": 20},
        }
        session.commit()
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "DEGRADED"
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"
        assert len(list(session.scalars(select(PaperOrder)))) == 1
        checks = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked",
        )))
        assert any(row.details["snapshot"].get("reason_code") == "EXIT_POLICY_INVALID"
                   for row in checks)


def test_expiry_test_entry_is_bounded_labeled_and_idempotent(client, monkeypatch):
    provider, headers, payload, trade, factory = setup_position(
        client, monkeypatch, expiry_test_only=True,
    )
    assert trade["strategy_name"].startswith("TEST ONLY")
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        assert saved.entry_rule_snapshot["testing_only"] is True
        assert saved.entry_rule_snapshot["exit_policy"]["version"] == "nifty-option-expiry-test-v1"
    assert client.post("/paper/trades", headers=headers, json=payload).json()["id"] == trade["id"]
    assert client.post("/paper/trades", headers={
        **headers, "Idempotency-Key": "second-expiry-test",
    }, json=payload).status_code == 409
    assert client.post("/paper/deployments", headers=headers, json=payload).status_code == 422
    for changes in ({"lots": 2}, {"strategy_key": "bear_call_spread"},
                    {"stop_loss_amount": "100"}, {"underlying": "SENSEX"}):
        assert client.post("/paper/trades", headers=headers,
                           json=payload | changes).status_code == 422
    assert client.get("/paper/expiry-test-policy").status_code == 401
    assert client.get("/paper/expiry-test-policy", headers=headers).json()["lots"] == 1
    provider.price_adjustment = Decimal("1000")
    expensive = client.post("/paper/trades", headers={
        **headers, "Idempotency-Key": "expensive-expiry-test",
    }, json=payload)
    assert expensive.status_code == 409
    assert "10000 rupees" in expensive.json()["detail"]
    assert client.get("/paper/account/ledger-reconciliation", headers=headers).json()[
        "status"
    ] == "MATCHED"


def test_expiry_test_full_settlement_cycle_isolated_and_repeat_safe(client, monkeypatch):
    provider, headers, payload, trade, factory = setup_position(
        client, monkeypatch, expiry_test_only=True,
    )
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    # Explicit historical-expiry fixture, never a mutation of the owner's account.
    expiry = date(2020, 1, 1)
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        saved.expiry = expiry
        session.commit()

    async def official_close(day):
        assert day == expiry
        return OfficialIndexClose(
            close=Decimal("25100.00"), source_url=archive_url(expiry),
            sha256="a" * 64, retrieved_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr("apps.api.main.fetch_nifty_close", official_close)
    endpoint = f"/paper/trades/{trade['id']}/expiry-settlement"
    preview = client.get(endpoint + "/official-preview", headers=headers)
    assert preview.status_code == 200
    assert preview.json()["preview"]["settlement_cash_flow"] == "2500.00"
    body = {"settlement_date": expiry.isoformat(), "final_index_value": "25100.00",
            "source_reference": archive_url(expiry), "archive_sha256": "a" * 64,
            "confirmed_final": True}
    rejected = client.post(endpoint, headers=headers,
                           json=body | {"archive_sha256": "b" * 64})
    assert rejected.status_code == 409
    settled = client.post(endpoint, headers=headers, json=body)
    assert settled.status_code == 200
    assert settled.json()["status"] == "CLOSED"
    assert Decimal(settled.json()["realized_pnl"]) == Decimal(trade["entry_cash_flow"]) + 2500
    assert client.post(endpoint, headers=headers, json=body).json()["id"] == trade["id"]
    # Restarting the worker must not reopen or close a settled test again.
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        assert saved.exit_reason == "EXPIRY_SETTLEMENT"
        assert saved.entry_rule_snapshot["testing_only"] is True
        assert len(list(session.scalars(select(PaperOrder)))) == 2
    report = client.get("/paper/account/ledger-reconciliation", headers=headers).json()
    assert report["status"] == "MATCHED"


def test_expiry_square_off_uses_trade_cutoff_after_runtime_change(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "paper_expiry_square_off_minutes_before_close", 20)
    provider, headers, _, trade, factory = setup_position(client, monkeypatch)
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        assert saved.entry_rule_snapshot["exit_policy"]["square_off_minutes_before_close"] == 20
        saved.expiry = exchange_today()
        session.commit()

    async def between_cutoffs(_trading_day):
        now = datetime.now(timezone.utc)
        return ExchangeSession("NFO", now - timedelta(hours=6), now + timedelta(minutes=25))

    monkeypatch.setattr(provider, "get_nfo_session", between_cutoffs)
    monkeypatch.setattr(settings, "paper_expiry_square_off_minutes_before_close", 30)
    assert asyncio.run(run_cycle(factory, provider, settings)) == "HEALTHY"
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"

    async def within_pinned_cutoff(_trading_day):
        now = datetime.now(timezone.utc)
        return ExchangeSession("NFO", now - timedelta(hours=6), now + timedelta(minutes=10))

    monkeypatch.setattr(provider, "get_nfo_session", within_pinned_cutoff)
    assert asyncio.run(run_cycle(factory, provider, settings)) == "HEALTHY"
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        assert saved.exit_reason == "EXPIRY_SQUARE_OFF"
    report = client.get(
        f"/paper/trades/{trade['id']}/exit-reconciliation", headers=headers
    )
    assert report.json()["status"] == "MATCHED"


def test_expiry_square_off_preserves_stop_loss_priority(client, monkeypatch):
    provider, _, _, trade, factory = setup_position(
        client, monkeypatch, stop_loss_amount="100",
    )
    with factory() as session:
        session.get(PaperTrade, UUID(trade["id"])).expiry = exchange_today()
        session.commit()

    async def closing_session(_trading_day):
        now = datetime.now(timezone.utc)
        return ExchangeSession("NFO", now - timedelta(hours=6), now + timedelta(minutes=10))

    monkeypatch.setattr(provider, "get_nfo_session", closing_session)
    provider.price_adjustment = Decimal(-10)
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).exit_reason == "STOP_LOSS"


def test_expiry_square_off_waits_until_cutoff_and_requires_verified_quote(client, monkeypatch):
    provider, _, _, trade, factory = _expiry_day_position(client, monkeypatch)

    async def early_session(_trading_day):
        now = datetime.now(timezone.utc)
        return ExchangeSession("NFO", now - timedelta(hours=6), now + timedelta(minutes=45))

    monkeypatch.setattr(provider, "get_nfo_session", early_session)
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"
    async def closing_session(_trading_day):
        now = datetime.now(timezone.utc)
        return ExchangeSession("NFO", now - timedelta(hours=6), now + timedelta(minutes=10))

    monkeypatch.setattr(provider, "get_nfo_session", closing_session)
    provider.timestamp_basis = "api_response_received"
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "DEGRADED"
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"
        assert len(list(session.scalars(select(PaperOrder)))) == 1


def test_expiry_day_entry_is_rejected_during_square_off_window(client, monkeypatch):
    provider = FakeUpstoxProvider()
    provider.expiry = exchange_today()
    provider.contracts = [
        contract.model_copy(update={"expiry": provider.expiry})
        for contract in provider.contracts
    ]

    async def closing_session(_trading_day):
        now = datetime.now(timezone.utc)
        return ExchangeSession("NFO", now - timedelta(hours=6), now + timedelta(minutes=10))

    monkeypatch.setattr(provider, "get_nfo_session", closing_session)
    monkeypatch.setattr("apps.api.main.get_market_data_provider", lambda: provider)
    headers = {**_token(client), "Idempotency-Key": "expiry-cutoff-entry"}
    response = client.post("/paper/trades", headers=headers, json={
        "strategy_key": "long_call", "expiry": provider.expiry.isoformat(),
        "reference_strike": "25000", "lots": 1,
    })
    assert response.status_code == 409
    assert "Expiry-day paper entry window" in response.json()["detail"]
    with client.app.state.test_session_factory() as session:
        assert list(session.scalars(select(PaperOrder))) == []


def test_trailing_peak_survives_new_worker_owner(client, monkeypatch):
    provider, _, _, trade, factory = setup_position(client, monkeypatch, trailing_stop_amount="200")
    provider.price_adjustment = Decimal(20)
    asyncio.run(run_cycle(factory, provider, get_settings(), owner="first"))
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).peak_unrealized_pnl == Decimal(475)
    provider.price_adjustment = Decimal(10)
    asyncio.run(run_cycle(factory, provider, get_settings(), owner="replacement"))
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).exit_reason == "TRAILING_STOP"


def test_exit_quote_path_and_stop_decisions_are_durable_and_account_scoped(client, monkeypatch):
    provider, headers, _, trade, factory = setup_position(
        client, monkeypatch, trailing_stop_amount="200"
    )
    provider.price_adjustment = Decimal(20)
    original_chain = provider.get_option_chain
    saved_chain = asyncio.run(original_chain("NIFTY", provider.expiry))

    async def unchanged_chain(*_args):
        return saved_chain

    provider.get_option_chain = unchanged_chain
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        observations = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        )))
        assert len(observations) == 1
        first = observations[0].details["snapshot"]
        assert first["decision"] == "HOLD"
        assert first["peak_unrealized_pnl"] == "475.00"
        assert first["market_as_of"] == saved_chain.as_of.isoformat()
        assert first["leg_quotes"][0]["selected_side_quote"] == first["leg_quotes"][0]["bid"]
        assert first["leg_quotes"][0]["quote_timestamp"] == (
            saved_chain.contracts[4].quote.timestamp.isoformat()
        )
        trade_time = saved_chain.contracts[4].quote.last_trade_time
        assert first["leg_quotes"][0]["last_trade_time"] == (
            trade_time.isoformat() if trade_time else None
        )
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"

    provider.get_option_chain = original_chain
    provider.price_adjustment = Decimal(10)
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        observations = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        ).order_by(AuditEvent.created_at, AuditEvent.id)))
        assert len(observations) == 2
        final = observations[-1].details["snapshot"]
        closed = session.get(PaperTrade, UUID(trade["id"]))
        assert final["decision"] == "TRAILING_STOP"
        assert Decimal(final["unrealized_pnl"]) == closed.realized_pnl
        assert closed.status == "CLOSED"

    assert client.get(f"/paper/trades/{trade['id']}/observations").status_code == 401
    response = client.get(f"/paper/trades/{trade['id']}/observations", headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert [item["details"]["snapshot"]["decision"] for item in response.json()] == [
        "TRAILING_STOP", "HOLD",
    ]
    reconciliation = client.get(
        f"/paper/trades/{trade['id']}/exit-reconciliation", headers=headers
    )
    assert reconciliation.status_code == 200
    assert reconciliation.json()["status"] == "MATCHED"
    assert reconciliation.json()["evaluated_count"] == 2
    second = client.post("/auth/register", json={
        "email": "another-paper@example.com", "password": "another-strong-password",
    })
    assert second.status_code == 201
    other_headers = {"Authorization": f"Bearer {second.json()['access_token']}"}
    assert client.get(
        f"/paper/trades/{trade['id']}/observations", headers=other_headers
    ).status_code == 404
    assert client.get(
        f"/paper/trades/{trade['id']}/exit-reconciliation", headers=other_headers
    ).status_code == 404


def test_data_outage_does_not_fabricate_exit_and_reports_degraded(client, monkeypatch):
    _, headers, _, trade, factory = setup_position(client, monkeypatch, stop_loss_amount="100")
    result = asyncio.run(run_cycle(factory, UnconfiguredMarketDataProvider(), get_settings()))
    assert result == "DEGRADED"
    assert asyncio.run(run_cycle(
        factory, UnconfiguredMarketDataProvider(), get_settings()
    )) == "DEGRADED"
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"
        skipped = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        )))
        assert len(skipped) == 1
        assert skipped[0].details["snapshot"]["decision"] == "SKIPPED"
        assert skipped[0].details["snapshot"]["reason_code"] == "SESSION_UNAVAILABLE"
        assert skipped[0].details["snapshot"]["unrealized_pnl"] is None
    health = client.get("/paper/worker", headers=headers).json()
    assert health["status"] == "DEGRADED"
    assert "Reason:" in health["detail"]


def test_bad_chain_and_missing_exit_depth_are_evidence_not_fills(client, monkeypatch):
    provider, headers, _, trade, factory = setup_position(
        client, monkeypatch, stop_loss_amount="100"
    )
    provider.crossed_market = True
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "DEGRADED"
    with factory() as session:
        saved = session.get(PaperTrade, UUID(trade["id"]))
        assert saved.status == "OPEN"
        observation = session.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        ))
        failed = observation.details["snapshot"]
        assert failed["decision"] == "SKIPPED"
        assert failed["reason_code"] == "CHAIN_QUALITY_FAIL"
        assert failed["quality_status"] == "FAIL"
        assert failed["leg_quotes"][0]["bid"] is not None
        assert failed["unrealized_pnl"] is None
        assert len(list(session.scalars(select(PaperOrder)))) == 1

    provider.crossed_market = False
    original_chain = provider.get_option_chain

    async def no_exit_depth(*args):
        chain = await original_chain(*args)
        return chain.model_copy(update={"contracts": [
            leg.model_copy(update={"bid_quantity": 0})
            if leg.strike == 25000 and leg.option_type == "CE" else leg
            for leg in chain.contracts
        ]})

    provider.get_option_chain = no_exit_depth
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "DEGRADED"
    with factory() as session:
        observations = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        ).order_by(AuditEvent.created_at, AuditEvent.id)))
        assert len(observations) == 2
        skipped = observations[-1].details["snapshot"]
        assert skipped["reason_code"] == "QUOTE_REJECTED"
        assert skipped["leg_quotes"][0]["bid_quantity"] == 0
        assert skipped["unrealized_pnl"] is None
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"
        assert len(list(session.scalars(select(PaperOrder)))) == 1

    provider.get_option_chain = original_chain
    provider.price_adjustment = Decimal(-10)
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "HEALTHY"
    with factory() as session:
        observations = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        )))
        assert len(observations) == 3
        assert any(row.details["snapshot"]["decision"] == "STOP_LOSS"
                   for row in observations)
        assert session.get(PaperTrade, UUID(trade["id"])).status == "CLOSED"
        assert len(list(session.scalars(select(PaperOrder)))) == 2
    report = client.get(
        f"/paper/trades/{trade['id']}/exit-reconciliation", headers=headers
    )
    assert report.json()["status"] == "INCOMPLETE"
    assert report.json()["skipped_count"] == 2


def test_unverified_source_timestamp_does_not_trigger_worker_exit(client, monkeypatch):
    provider, _, _, trade, factory = setup_position(client, monkeypatch, stop_loss_amount="100")
    provider.price_adjustment = Decimal(-10)
    provider.timestamp_basis = "api_response_received"
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "DEGRADED"
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"
        assert len(list(session.scalars(select(PaperOrder)))) == 1
        observation = session.scalar(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        ))
        assert observation.details["snapshot"]["reason_code"] == "QUOTE_REJECTED"


def test_competing_worker_is_blocked_and_expired_owner_is_fenced(client, monkeypatch):
    provider, _, _, trade, factory = setup_position(client, monkeypatch, stop_loss_amount="100")
    with factory() as session:
        old = acquire(session, "old-worker", 60)
    assert old is not None
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "BUSY"
    with factory() as session:
        session.execute(
            update(WorkerLease).values(
                lease_until=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )
        session.commit()
        new = acquire(session, "new-worker", 60)
        assert new.generation > old.generation
    provider.price_adjustment = Decimal(-10)
    with factory() as session:
        user_id = session.get(PaperTrade, UUID(trade["id"])).user_id
        unavailable = asyncio.run(monitor_exits(
            user_id, session, UnconfiguredMarketDataProvider(), get_settings(), old
        ))
        assert "fenced off" in unavailable.warnings[-1]
        assert list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        ))) == []
    with factory() as session:
        user_id = session.get(PaperTrade, UUID(trade["id"])).user_id
        result = asyncio.run(monitor_exits(user_id, session, provider, get_settings(), old))
        assert not result.closed_trade_ids
        assert "fenced" in result.warnings[-1]
        assert list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "paper.trade.exit_checked"
        ))) == []
    with factory() as session:
        result = asyncio.run(monitor_exits(user_id, session, provider, get_settings(), new))
        assert result.closed_trade_ids == [UUID(trade["id"])]


def test_worker_heartbeat_becomes_stale(client, monkeypatch):
    provider, headers, _, _, factory = setup_position(client, monkeypatch)
    asyncio.run(run_cycle(factory, provider, get_settings()))
    with factory() as session:
        session.execute(
            update(WorkerLease)
            .where(WorkerLease.name == WORKER_NAME)
            .values(
                heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        )
        session.commit()
    assert client.get("/paper/worker", headers=headers).json()["status"] == "STALE"


def test_entry_requires_key_and_rejects_changed_payload(client, monkeypatch):
    _, headers, payload, _, _ = setup_position(client, monkeypatch)
    assert (
        client.post(
            "/paper/trades", headers={"Authorization": headers["Authorization"]}, json=payload
        ).status_code
        == 422
    )
    changed = client.post("/paper/trades", headers=headers, json={**payload, "lots": 2})
    assert changed.status_code == 409
    assert "different entry" in changed.json()["detail"]


def test_entry_replay_survives_provider_outage_and_kill_switch(client, monkeypatch):
    _, headers, payload, trade, _ = setup_position(client, monkeypatch)
    client.put("/paper/account/kill-switch", headers=headers, json={"enabled": True})
    monkeypatch.setattr("apps.api.main.get_market_data_provider", UnconfiguredMarketDataProvider)
    replay = client.post("/paper/trades", headers=headers, json=payload)
    assert replay.status_code == 201
    assert replay.json() == trade
    assert len(client.get("/paper/orders", headers=headers).json()) == 1


def test_unverified_quotes_block_by_default(client, monkeypatch):
    provider, headers, payload, _, _ = setup_position(client, monkeypatch)
    provider.timestamp_basis = "api_response_received"
    blocked = client.post(
        "/paper/trades", headers={**headers, "Idempotency-Key": "new-entry-002"}, json=payload
    )
    assert blocked.status_code == 409
    assert "unverified" in blocked.json()["detail"]


@pytest.mark.parametrize("offset", [-3600, 3600])
def test_individual_quote_timestamp_is_checked(client, monkeypatch, offset):
    provider, headers, payload, _, _ = setup_position(client, monkeypatch)
    original = provider.get_option_chain

    async def bad_quote(underlying, expiry):
        chain = await original(underlying, expiry)
        for contract in chain.contracts:
            contract.quote.timestamp += timedelta(seconds=offset)
        return chain

    provider.get_option_chain = bad_quote
    blocked = client.post(
        "/paper/trades", headers={**headers, "Idempotency-Key": "bad-time-001"}, json=payload
    )
    assert blocked.status_code == 409


def test_failed_worker_does_not_expose_provider_error(client, monkeypatch):
    provider, headers, _, _, factory = setup_position(client, monkeypatch)

    async def broken(*args):
        raise RuntimeError("sensitive-token-must-not-be-logged")

    provider.get_option_chain = broken
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "ERROR"
    health = client.get("/paper/worker", headers=headers).json()
    assert "sensitive" not in health["detail"]


def test_disabled_worker_preserves_positions(client, monkeypatch):
    provider, _, _, trade, factory = setup_position(client, monkeypatch, stop_loss_amount="100")
    provider.price_adjustment = Decimal(-10)
    settings = get_settings().model_copy(update={"paper_trading_enabled": False})
    assert asyncio.run(run_cycle(factory, provider, settings)) == "DISABLED"
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"


def test_expired_lease_has_no_mutation_authority(client):
    factory = client.app.state.test_session_factory
    with factory() as session:
        token = acquire(session, "worker", 60)
        session.execute(
            update(WorkerLease).values(
                lease_until=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )
        session.commit()
        assert not assert_owned(session, token)


def test_old_worker_cannot_overwrite_new_owner_health(client):
    factory = client.app.state.test_session_factory
    with factory() as session:
        old = acquire(session, "old-worker", 60)
        session.execute(
            update(WorkerLease).values(
                lease_until=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        )
        session.commit()
        new = acquire(session, "new-worker", 60)
        finish(session, old, "ERROR", "stale result")
        lease = session.get(WorkerLease, WORKER_NAME)
        assert lease.owner == new.owner
        assert lease.generation == new.generation
        assert lease.status == "RUNNING"


def test_quote_expiring_before_entry_commit_does_not_create_order(client, monkeypatch):
    provider, headers, payload, _, factory = setup_position(client, monkeypatch)
    original_chain = provider.get_option_chain
    latest_chain = []

    async def capture_chain(underlying, expiry):
        chain = await original_chain(underlying, expiry)
        latest_chain.append(chain)
        return chain

    def expire_before_lock(session, user_id):
        chain = latest_chain[-1]
        chain.as_of -= timedelta(minutes=5)
        for contract in chain.contracts:
            contract.quote.timestamp -= timedelta(minutes=5)
        return lock_account(session, user_id)

    provider.get_option_chain = capture_chain
    monkeypatch.setattr("apps.api.main.lock_account", expire_before_lock)
    blocked = client.post(
        "/paper/trades", headers={**headers, "Idempotency-Key": "late-entry-quote"}, json=payload
    )
    assert blocked.status_code == 409
    with factory() as session:
        assert len(list(session.scalars(select(PaperTrade)))) == 1
        assert len(list(session.scalars(select(PaperOrder)))) == 1


def test_quote_expiring_before_worker_commit_preserves_open_position(client, monkeypatch):
    provider, _, _, trade, factory = setup_position(client, monkeypatch, stop_loss_amount="100")
    provider.price_adjustment = Decimal(-10)
    original_chain = provider.get_option_chain
    latest_chain = []

    async def capture_chain(underlying, expiry):
        chain = await original_chain(underlying, expiry)
        latest_chain.append(chain)
        return chain

    def expire_before_lock(session, user_id):
        chain = latest_chain[-1]
        chain.as_of -= timedelta(minutes=5)
        for contract in chain.contracts:
            contract.quote.timestamp -= timedelta(minutes=5)
        return lock_account(session, user_id)

    provider.get_option_chain = capture_chain
    monkeypatch.setattr("apps.api.services.paper_monitor.lock_account", expire_before_lock)
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "DEGRADED"
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"
        assert len(list(session.scalars(select(PaperOrder)))) == 1


def test_open_position_does_not_display_pnl_from_stale_leg_quote(client, monkeypatch):
    provider, headers, _, trade, _ = setup_position(client, monkeypatch)
    provider.price_adjustment = Decimal(10)
    selected_instrument = trade["legs"][0]["instrument_key"]
    original_chain = provider.get_option_chain

    async def chain_with_stale_selected_quote(underlying, expiry):
        chain = await original_chain(underlying, expiry)
        for contract in chain.contracts:
            if contract.instrument_token == selected_instrument:
                contract.quote.timestamp -= timedelta(minutes=5)
        return chain

    provider.get_option_chain = chain_with_stale_selected_quote
    listed = client.get("/paper/trades", headers=headers)
    assert listed.status_code == 200
    item = listed.json()["trades"][0]
    assert item["unrealized_pnl"] is None
    assert "stale" in item["valuation_warning"]


def test_expired_position_stays_unresolved_without_synthetic_fill(client, monkeypatch):
    provider, headers, payload, trade, factory = setup_position(
        client, monkeypatch, stop_loss_amount="100"
    )
    monkeypatch.setattr(
        "packages.paper_trading.session.exchange_today",
        lambda now=None: provider.expiry + timedelta(days=1),
    )

    async def forbidden_chain(*args):
        raise AssertionError("expired contracts must not be requested for paper fills")

    provider.get_option_chain = forbidden_chain
    new_entry = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": "expired-new-entry"},
        json=payload,
    )
    assert new_entry.status_code == 409
    assert "expiry has passed" in new_entry.json()["detail"]

    replay = client.post("/paper/trades", headers=headers, json=payload)
    assert replay.status_code == 201
    assert replay.json() == trade

    positions = client.get("/paper/trades", headers=headers)
    assert positions.status_code == 200
    assert "unresolved after expiry" in positions.json()["warnings"][0]
    assert positions.json()["trades"][0]["unrealized_pnl"] is None
    assert "settlement" in positions.json()["trades"][0]["valuation_warning"]

    close = client.post(f"/paper/trades/{trade['id']}/close", headers=headers)
    assert close.status_code == 409
    assert "reconcile settlement" in close.json()["detail"]
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "DEGRADED"
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"
        assert len(list(session.scalars(select(PaperOrder)))) == 1


def test_closed_nfo_session_blocks_paper_fills_and_marks(client, monkeypatch):
    provider, headers, payload, trade, factory = setup_position(
        client, monkeypatch, stop_loss_amount="100"
    )

    async def closed_session(trading_day):
        return None

    async def forbidden_chain(*args):
        raise AssertionError("closed session must not request executable quotes")

    provider.get_nfo_session = closed_session
    provider.get_option_chain = forbidden_chain
    new_entry = client.post(
        "/paper/trades",
        headers={**headers, "Idempotency-Key": "closed-session-entry"},
        json=payload,
    )
    assert new_entry.status_code == 409
    assert "no scheduled trading session" in new_entry.json()["detail"]

    positions = client.get("/paper/trades", headers=headers)
    assert positions.status_code == 200
    assert "session is closed or unverified" in positions.json()["warnings"][0]
    assert positions.json()["trades"][0]["unrealized_pnl"] is None

    close = client.post(f"/paper/trades/{trade['id']}/close", headers=headers)
    assert close.status_code == 409
    assert asyncio.run(run_cycle(factory, provider, get_settings())) == "DEGRADED"
    with factory() as session:
        assert session.get(PaperTrade, UUID(trade["id"])).status == "OPEN"
        assert len(list(session.scalars(select(PaperOrder)))) == 1
