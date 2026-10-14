import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from apps.api.services.equity_runtime import process_equities
from packages.database.models import (
    AuditEvent,
    EquityDeployment,
    EquityPolicyTrial,
    EquityValidationStudy,
    User,
    WatchlistItem,
)
from packages.market_data.contracts import ExchangeSession, HistoricalBar, Quote
from packages.paper_trading.equity import (
    completed_bars,
    entry_threshold,
    exit_reason,
    open_position,
    replay,
    signal,
    trail,
)
from packages.paper_trading.worker_lease import acquire, finish


class Clock(datetime):
    instant = datetime(2026, 9, 29, 5, 30, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.instant if tz else cls.instant.replace(tzinfo=None)


class Provider:
    provider_id = "test-provider"

    def __init__(self):
        self.day = Clock.instant - timedelta(days=1)
        self.price = Decimal("160")
        self.stale = False
        self.closed = False
        self.fail_history = False
        self.bad_key = False
        self.quote_basis = "exchange"
        self.book_basis = "exchange"
        self.book_lag_seconds = 0
        self.response_lag_seconds = 0
        self.bid_quantity = 1000
        self.ask_quantity = 1000
        self.quote_requests = 0

    async def get_historical(self, key, start, end, interval):
        assert interval == "1d"
        if self.fail_history:
            raise RuntimeError("secret-provider-token-must-not-leak")
        return [
            HistoricalBar(
                timestamp=self.day - timedelta(days=59 - i),
                open=100 + i,
                high=102 + i,
                low=99 + i,
                close=101 + i,
                volume=10000,
            )
            for i in range(60)
        ]

    async def get_quote(self, key):
        self.quote_requests += 1
        return Quote(
            instrument_token="wrong" if self.bad_key else key,
            timestamp=Clock.instant - timedelta(seconds=self.response_lag_seconds),
            timestamp_basis=self.quote_basis,
            book_as_of=Clock.instant - timedelta(seconds=self.book_lag_seconds),
            book_timestamp_basis=self.book_basis,
            last_trade_time=Clock.instant - timedelta(seconds=120 if self.stale else 1),
            last_price=self.price,
            bid=self.price,
            ask=self.price + Decimal("0.10"),
            bid_quantity=self.bid_quantity,
            ask_quantity=self.ask_quantity,
        )

    async def get_equity_session(self, day):
        if self.closed:
            return None
        return ExchangeSession(
            "NSE", Clock.instant - timedelta(hours=1), Clock.instant + timedelta(hours=1)
        )


@pytest.fixture
def setup(client, monkeypatch):
    Clock.instant = datetime(2026, 9, 29, 5, 30, tzinfo=timezone.utc)
    provider = Provider()
    monkeypatch.setattr("apps.api.equity_routes.datetime", Clock)
    monkeypatch.setattr("apps.api.services.equity_runtime.datetime", Clock)
    monkeypatch.setattr("apps.api.equity_routes.get_market_data_provider", lambda: provider)
    response = client.post(
        "/auth/register", json={"email": "equity@example.com", "password": "test-strong-password"}
    )
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    factory = client.app.state.test_session_factory
    with factory() as session:
        user = session.scalar(select(User).where(User.email == "equity@example.com"))
        item = WatchlistItem(
            user_id=user.id,
            provider=provider.provider_id,
            instrument_key="NSE_EQ|TEST",
            trading_symbol="TEST",
            exchange="NSE",
            segment="NSE_EQ",
            instrument_type="EQ",
        )
        session.add(item)
        session.commit()
        item_id = str(item.id)
    body = dict(
        watchlist_id=item_id,
        strategy="trend",
        command_key=str(uuid4()),
        budget="10000",
        risk_budget="200",
    )
    return headers, body, provider, factory


def cycle(provider, factory):
    with factory() as session:
        token = acquire(session, str(uuid4()), 120)
    assert token
    result = asyncio.run(process_equities(factory, provider, token))
    with factory() as session:
        finish(session, token, "HEALTHY", "Test cycle")
    return result


def account(client, headers):
    return client.get("/equity/deployments", headers=headers).json()


def test_bollinger_reference_is_not_a_complete_entry_signal():
    provider = Provider()
    bars = asyncio.run(provider.get_historical(
        "NSE_EQ|TEST", provider.day - timedelta(days=60), provider.day, "1d",
    ))
    assert bars[-1].close > entry_threshold("band_reclaim", bars)
    assert signal("band_reclaim", bars) is False


@pytest.mark.parametrize("keep_threshold", [False, True])
def test_closed_session_restores_daily_state_for_older_deployment(
    client, setup, keep_threshold
):
    headers, body, provider, factory = setup
    provider.closed = True
    applied = client.post("/equity/deployments", json=body, headers=headers).json()
    assert Decimal(applied["state"]["entry_threshold_price"]) > 0
    with factory() as session:
        row = session.get(EquityDeployment, UUID(applied["id"]))
        state = dict(row.state)
        fields = [
            "latest_daily_signal_date", "latest_daily_signal_met",
            "latest_daily_signal_after_apply",
        ]
        if not keep_threshold:
            fields.append("entry_threshold_price")
        for field in fields:
            state.pop(field, None)
        row.state = state
        session.commit()
    cycle(provider, factory)
    waiting = account(client, headers)["deployments"][0]
    assert waiting["status"] == "WAITING_FOR_ENTRY"
    assert Decimal(waiting["state"]["entry_threshold_price"]) > 0
    assert waiting["state"]["latest_daily_signal_date"] == applied["state"]["signal_after"]
    assert isinstance(waiting["state"]["latest_daily_signal_met"], bool)
    assert waiting["state"]["latest_daily_signal_after_apply"] is False
    assert "open NSE session" in waiting["state"]["detail"]


def test_equity_worker_shares_quote_once_per_symbol_in_a_cycle(client, setup):
    headers, body, provider, factory = setup
    first = client.post("/equity/deployments", json=body, headers=headers)
    assert first.status_code == 201, first.text
    second_auth = client.post(
        "/auth/register",
        json={"email": "second-equity@example.com", "password": "test-strong-password"},
    )
    other_headers = {"Authorization": "Bearer " + second_auth.json()["access_token"]}
    with factory() as session:
        other = session.scalar(select(User).where(User.email == "second-equity@example.com"))
        item = WatchlistItem(
            user_id=other.id, provider=provider.provider_id,
            instrument_key="NSE_EQ|TEST", trading_symbol="TEST", exchange="NSE",
            segment="NSE_EQ", instrument_type="EQ",
        )
        session.add(item)
        session.commit()
        other_item_id = str(item.id)
    second = client.post(
        "/equity/deployments",
        json={**body, "watchlist_id": other_item_id, "command_key": str(uuid4())},
        headers=other_headers,
    )
    assert second.status_code == 201, second.text
    cycle(provider, factory)
    assert provider.quote_requests == 1
    assert account(client, headers)["deployments"][0]["status"] == "WAITING_FOR_ENTRY"
    assert account(client, other_headers)["deployments"][0]["status"] == "WAITING_FOR_ENTRY"


def test_equity_ledger_reconciles_lifecycle_and_detects_saved_fill_corruption(
    client, setup, monkeypatch
):
    headers, body, provider, factory = setup
    endpoint = "/equity/account/ledger-reconciliation"
    assert client.get(endpoint).status_code == 401
    empty = client.get(endpoint, headers=headers).json()
    assert empty["status"] == "MATCHED"
    assert empty["cash_journal"]["status"] == "NOT_STARTED"

    applied = client.post("/equity/deployments", json=body, headers=headers)
    assert applied.status_code == 201, applied.text
    waiting = client.get(endpoint, headers=headers)
    assert waiting.headers["cache-control"] == "no-store"
    assert waiting.json()["status"] == "MATCHED"
    assert waiting.json()["expected_cash"] == "990000.00"
    assert waiting.json()["reserved_count"] == 1
    assert waiting.json()["cash_journal"]["status"] == "MATCHED"
    assert waiting.json()["cash_journal"]["entry_count"] == 2
    monkeypatch.setenv("INITIAL_EQUITY_PAPER_CAPITAL", "2000000")
    from apps.api.settings import get_settings

    get_settings.cache_clear()
    still_funded_from_saved_balance = client.get(endpoint, headers=headers).json()
    assert still_funded_from_saved_balance["status"] == "MATCHED"
    assert still_funded_from_saved_balance["initial_cash"] == "1000000.00"
    monkeypatch.setenv("INITIAL_EQUITY_PAPER_CAPITAL", "1000000")
    get_settings.cache_clear()

    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    cycle(provider, factory)
    opened = client.get(endpoint, headers=headers).json()
    assert opened["status"] == "MATCHED"
    assert opened["reserved_count"] == 1

    position = account(client, headers)["deployments"][0]["state"]["position"]
    provider.price = Decimal(position["target"]) + Decimal("1")
    Clock.instant += timedelta(seconds=1)
    cycle(provider, factory)
    closed = client.get(endpoint, headers=headers).json()
    assert closed["status"] == "MATCHED"
    assert closed["closed_count"] == 1 and closed["reserved_count"] == 0
    assert closed["expected_cash"] == account(client, headers)["cash"]
    assert closed["cash_journal"]["status"] == "MATCHED"
    assert closed["cash_journal"]["entry_count"] == 3

    with factory() as session:
        row = session.get(EquityDeployment, UUID(applied.json()["id"]))
        row.state = {**row.state, "realized_pnl": "99999.00"}
        session.commit()
    corrupted = client.get(endpoint, headers=headers).json()
    assert corrupted["status"] == "MISMATCH"
    assert corrupted["issue_count"] == 2
    assert any("saved fill" in issue for issue in corrupted["issues"])
    assert corrupted["cash_difference"] != "0.00"

    other = client.post("/auth/register", json={
        "email": "other-equity-ledger@example.com", "password": "test-strong-password",
    })
    other_headers = {"Authorization": "Bearer " + other.json()["access_token"]}
    isolated = client.get(endpoint, headers=other_headers).json()
    assert isolated["status"] == "MATCHED"
    assert isolated["deployment_count"] == 0
    assert isolated["cash_journal"]["status"] == "NOT_STARTED"


def test_dashboard_totals_cover_older_trades_and_are_account_scoped(client, setup):
    headers, _, provider, factory = setup
    with factory() as session:
        user = session.scalar(select(User).where(User.email == "equity@example.com"))
        for index in range(205):
            session.add(EquityDeployment(
                user_id=user.id, command_key=f"dashboard-{index}", request_hash="0" * 64,
                provider=provider.provider_id, instrument_key="NSE_EQ|TEST", symbol="TEST",
                strategy="trend" if index < 204 else "breakout", version="v1",
                budget=Decimal("10000"), risk_budget=Decimal("200"), status="CLOSED",
                state={"realized_pnl": "10.00" if index < 204 else "-5.00"},
                created_at=Clock.instant + timedelta(seconds=index),
                updated_at=Clock.instant + timedelta(seconds=index),
            ))
        session.commit()

    assert len(account(client, headers)["deployments"]) == 200
    response = client.get("/equity/dashboard", headers=headers)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    summary = response.json()
    assert summary["scope"] == "all_time"
    assert summary["totals"]["realized_net"] == "2035.00"
    assert summary["totals"]["closed_trades"] == 205
    assert summary["totals"]["winning_trades"] == 204
    assert {item["strategy"]: item["realized_net"] for item in summary["strategies"]} == {
        "breakout": "-5.00", "trend": "2040.00",
    }
    assert sum(item["closed_trades"] for item in summary["daily"]) == 205
    assert sum(Decimal(item["realized_net"]) for item in summary["daily"]) == Decimal("2035.00")
    assert {item["strategy"] for item in summary["daily"]} == {"trend", "breakout"}

    other = client.post("/auth/register", json={
        "email": "other-equity@example.com", "password": "test-strong-password",
    })
    other_headers = {"Authorization": "Bearer " + other.json()["access_token"]}
    assert client.get("/equity/dashboard", headers=other_headers).json()["totals"][
        "closed_trades"
    ] == 0
    assert client.get("/equity/dashboard", headers=other_headers).json()["daily"] == []


def long_validation_history(provider):
    last = Clock.instant - timedelta(days=1)

    async def long_history(_key, _start, _end, interval):
        assert interval == "1d"
        return [HistoricalBar(
            timestamp=last - timedelta(days=759 - index),
            open=Decimal(100 + index), high=Decimal(102 + index),
            low=Decimal(99 + index), close=Decimal(101 + index), volume=10000,
        ) for index in range(760)]

    async def completed_session(day):
        if day != last.date():
            return None
        return ExchangeSession("NSE", last - timedelta(hours=6), last)

    provider.get_historical = long_history
    provider.get_equity_session = completed_session


def test_chronological_validation_is_read_only_and_reserves_latest_window(client, setup):
    headers, body, provider, factory = setup
    long_validation_history(provider)
    request = {key: body[key] for key in ("watchlist_id", "budget", "risk_budget")}
    assert client.post("/equity/chronological-validation", json=request).status_code == 401
    response = client.post("/equity/chronological-validation", json=request, headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert result["status"] == "ready"
    assert result["provider"] == provider.provider_id
    assert result["bars"] == 760 and result["reserved_bars"] == 152
    assert len(result["rows"]) == 10
    assert result["ranking_changed"] is False
    assert result["parameters_changed"] is False
    assert result["frozen"] is False
    with factory() as session:
        assert list(session.scalars(select(EquityDeployment))) == []
        assert list(session.scalars(select(EquityValidationStudy))) == []


def test_validation_attempts_retain_insufficient_and_failed_runs_without_paper_changes(
    client, setup,
):
    headers, body, provider, factory = setup
    request = {key: body[key] for key in ("watchlist_id", "budget", "risk_budget")}
    long_validation_history(provider)
    first = client.post("/equity/chronological-validation", json=request, headers=headers)
    assert first.status_code == 200 and first.json()["frozen"] is False

    async def failure(*_args):
        raise RuntimeError("sensitive-provider-error-must-not-be-recorded")

    provider.get_historical = failure
    second = client.post("/equity/chronological-validation", json=request, headers=headers)
    assert second.status_code == 200 and second.json()["status"] == "unavailable"
    history = client.get("/equity/validation-attempts", headers=headers)
    assert history.status_code == 200
    assert history.headers["cache-control"] == "no-store"
    attempts = history.json()
    assert len(attempts) == 2
    assert {row["status"] for row in attempts} == {"ready", "unavailable"}
    assert all(not row["frozen"] for row in attempts)
    assert all(row["requested_allocation"] == "10000" for row in attempts)
    assert "sensitive-provider-error" not in history.text
    assert len(client.get("/equity/validation-attempts?limit=1", headers=headers).json()) == 1
    assert client.get("/equity/validation-attempts?limit=201", headers=headers).status_code == 422
    assert client.get("/equity/validation-attempts").status_code == 401
    token = client.post("/auth/register", json={
        "email": "attempt-isolation@example.com", "password": "another-strong-password",
    }).json()["access_token"]
    assert client.get("/equity/validation-attempts", headers={
        "Authorization": f"Bearer {token}",
    }).json() == []
    with factory() as session:
        assert list(session.scalars(select(EquityDeployment))) == []
        assert list(session.scalars(select(EquityValidationStudy))) == []


def test_missing_calculation_source_records_failure_without_freezing(client, setup, monkeypatch):
    headers, body, provider, factory = setup
    long_validation_history(provider)

    def missing_source():
        raise OSError("private-path-or-provider-secret-must-not-leak")

    monkeypatch.setattr("apps.api.equity_routes.research_code_digest", missing_source)
    request = {key: body[key] for key in ("watchlist_id", "budget", "risk_budget")}
    response = client.post("/equity/chronological-validation", json=request, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["frozen"] is False
    assert "source identity" in response.json()["reason"]
    history = client.get("/equity/validation-attempts", headers=headers)
    assert len(history.json()) == 1
    assert history.json()[0]["status"] == "unavailable"
    assert "private-path-or-provider-secret" not in response.text + history.text
    with factory() as session:
        assert list(session.scalars(select(EquityValidationStudy))) == []
        assert list(session.scalars(select(EquityDeployment))) == []


def test_reviewable_study_freezes_once_and_survives_provider_outage(client, setup, monkeypatch):
    from packages.regime.chronological_validation import review_fixed_equity_rules

    headers, body, provider, factory = setup
    long_validation_history(provider)
    request = {key: body[key] for key in ("watchlist_id", "budget", "risk_budget")}

    def reviewable(*args, **kwargs):
        report = review_fixed_equity_rules(*args, **kwargs)
        first = report.rows[0].model_copy(update={
            "status": "reviewable", "training_trades": 20, "validation_trades": 20,
        })
        return report.model_copy(update={"rows": [first, *report.rows[1:]]})

    monkeypatch.setattr("apps.api.equity_routes.review_fixed_equity_rules", reviewable)
    first = client.post("/equity/chronological-validation", json=request, headers=headers)
    assert first.status_code == 200, first.text
    saved = first.json()
    assert saved["frozen"] is True
    assert saved["study_id"] and len(saved["dataset_digest"]) == 64
    assert saved["allocation"] == "10000"

    async def unavailable(*_args):
        raise RuntimeError("provider unavailable after freeze")

    provider.get_historical = unavailable
    changed_request = {**request, "budget": "12000"}
    second = client.post(
        "/equity/chronological-validation", json=changed_request, headers=headers
    )
    assert second.status_code == 200
    assert second.json() == saved  # Frozen assumptions never silently change.
    attempts = client.get("/equity/validation-attempts", headers=headers).json()
    assert len(attempts) == 2
    assert len({row["result_digest"] for row in attempts}) == 1
    assert all(row["served_allocation"] == "10000" for row in attempts)
    assert {row["requested_allocation"] for row in attempts} == {"10000", "12000"}
    with factory() as session:
        studies = list(session.scalars(select(EquityValidationStudy)))
        assert len(studies) == 1
        assert len(studies[0].dataset_manifest["bars"]) == 760
        assert studies[0].dataset_manifest["instrument_key"] == "NSE_EQ|TEST"
        events = list(session.scalars(select(AuditEvent).where(
            AuditEvent.event_type == "equity.validation.study_frozen"
        )))
        assert len(events) == 1

    other = client.post("/auth/register", json={
        "email": "other-validation@example.com", "password": "another-strong-password",
    }).json()
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    with factory() as session:
        second_user = session.scalar(select(User).where(
            User.email == "other-validation@example.com"
        ))
        other_item = WatchlistItem(
            user_id=second_user.id, provider=provider.provider_id,
            instrument_key="NSE_EQ|TEST", trading_symbol="TEST", exchange="NSE",
            segment="NSE_EQ", instrument_type="EQ",
        )
        session.add(other_item)
        session.commit()
        other_id = str(other_item.id)
    other_result = client.post(
        "/equity/chronological-validation", headers=other_headers,
        json={**request, "watchlist_id": other_id},
    )
    assert other_result.status_code == 200
    assert other_result.json()["frozen"] is False
    assert other_result.json()["status"] == "unavailable"


def test_study_provenance_is_owned_integrity_checked_and_omits_candles(client, setup, monkeypatch):
    from packages.regime.chronological_validation import review_fixed_equity_rules

    headers, body, provider, factory = setup
    long_validation_history(provider)

    def reviewable(*args, **kwargs):
        report = review_fixed_equity_rules(*args, **kwargs)
        return report.model_copy(update={"rows": [
            report.rows[0].model_copy(update={"status": "reviewable"}), *report.rows[1:],
        ]})

    monkeypatch.setattr("apps.api.equity_routes.review_fixed_equity_rules", reviewable)
    report = client.post("/equity/chronological-validation", headers=headers, json={
        key: body[key] for key in ("watchlist_id", "budget", "risk_budget")
    }).json()
    path = f"/equity/validation-studies/{report['study_id']}/provenance"
    assert client.get(path).status_code == 401
    result = client.get(path, headers=headers)
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    data = result.json()
    assert data["dataset_digest"] == report["dataset_digest"]
    assert data["missing_assumptions"] == []
    assert "calculation_environment" in data["assumptions"]
    assert "bars" not in result.text and "reserved" not in data
    other = client.post("/auth/register", json={
        "email": "provenance-other@example.com", "password": "another-strong-password",
    }).json()["access_token"]
    assert client.get(path, headers={"Authorization": f"Bearer {other}"}).status_code == 404
    # A valid older manifest stays readable without inventing runtime metadata.
    from apps.api.equity_routes import _canonical_hash

    with factory() as session:
        study = session.get(EquityValidationStudy, UUID(report["study_id"]))
        legacy = dict(study.dataset_manifest)
        legacy.pop("calculation_environment")
        study.dataset_manifest = legacy
        study.dataset_digest = _canonical_hash(legacy)
        study.report = {**study.report, "dataset_digest": study.dataset_digest}
        study.report_digest = _canonical_hash(study.report)
        session.commit()
    legacy_response = client.get(path, headers=headers)
    assert legacy_response.status_code == 200
    assert legacy_response.json()["missing_assumptions"] == ["calculation_environment"]
    assert "calculation_environment" not in legacy_response.json()["assumptions"]
    with factory() as session:
        study = session.get(EquityValidationStudy, UUID(report["study_id"]))
        study.dataset_manifest = {**study.dataset_manifest, "fee_per_side": "0.9"}
        session.commit()
    assert client.get(path, headers=headers).status_code == 409


@pytest.mark.parametrize("mode", ["ready", "insufficient", "boundary"])
def test_source_successor_preserves_original_and_reserved_window(client, setup, monkeypatch, mode):
    from packages.regime.chronological_validation import review_fixed_equity_rules

    headers, body, provider, factory = setup
    long_validation_history(provider)

    def reviewable(*args, **kwargs):
        report = review_fixed_equity_rules(*args, **kwargs)
        return report.model_copy(update={"rows": [
            report.rows[0].model_copy(update={"status": "reviewable"}), *report.rows[1:],
        ]})

    monkeypatch.setattr("apps.api.equity_routes.review_fixed_equity_rules", reviewable)
    request = {key: body[key] for key in ("watchlist_id", "budget", "risk_budget")}
    original = client.post("/equity/chronological-validation", json=request, headers=headers).json()
    path = f"/equity/validation-studies/{original['study_id']}/successor"
    note = {"reason": "Reviewed calculation source update"}
    assert client.post(path, json=note).status_code == 401
    assert client.post(path, json=note, headers=headers).status_code == 409
    monkeypatch.setattr("apps.api.equity_routes.research_code_digest", lambda: "c" * 64)

    async def forbidden_provider_call(*_args):
        raise AssertionError("Successor must use saved data, not provider history")

    provider.get_historical = forbidden_provider_call
    if mode != "ready":
        def changed_review(*args, **kwargs):
            report = reviewable(*args, **kwargs)
            if mode == "boundary":
                return report.model_copy(update={
                    "reserved_from": report.reserved_from + timedelta(days=1),
                })
            return report.model_copy(update={"rows": [
                row.model_copy(update={"status": "insufficient"}) for row in report.rows
            ]})

        monkeypatch.setattr("apps.api.equity_routes.review_fixed_equity_rules", changed_review)
    result = client.post(path, json=note, headers=headers)
    if mode != "ready":
        assert result.status_code == (409 if mode == "boundary" else 200)
        if mode == "insufficient":
            assert result.json()["frozen"] is False and result.json()["generation"] == 2
        with factory() as session:
            assert len(list(session.scalars(select(EquityValidationStudy)))) == 1
            assert list(session.scalars(select(EquityDeployment))) == []
            attempts = list(session.scalars(select(AuditEvent).where(
                AuditEvent.event_type == "equity.validation.successor_attempt",
            )))
            assert len(attempts) == 1
            if mode == "boundary":
                assert attempts[0].details["status"] == "boundary_rejected"
        return
    assert result.status_code == 200, result.text
    successor = result.json()
    assert successor["frozen"] and successor["generation"] == 2
    assert successor["parent_study_id"] == original["study_id"]
    for key in ("training_from", "training_to", "validation_from", "validation_to",
                "reserved_from", "reserved_to", "reserved_bars"):
        assert successor[key] == original[key]
    for key in ("allocation", "planned_risk"):
        assert Decimal(successor[key]) == Decimal(original[key])
    assert client.post(path, json=note, headers=headers).json() == successor
    read_path = f"/equity/validation-studies/{successor['study_id']}"
    assert client.get(read_path, headers=headers).json() == successor
    assert client.get(read_path).status_code == 401
    comparison = client.post("/equity/exit-policy-comparison", headers=headers, json={
        "watchlist_id": body["watchlist_id"], "strategy": "trend",
        "study_id": successor["study_id"],
    })
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()["study_id"] == successor["study_id"]
    assert comparison.json()["holdout_evaluated"] is False
    assert client.post(
        "/equity/chronological-validation", json=request, headers=headers,
    ).json() == original
    other_token = client.post("/auth/register", json={
        "email": "successor-isolation@example.com", "password": "another-strong-password",
    }).json()["access_token"]
    assert client.post(path, json=note, headers={
        "Authorization": f"Bearer {other_token}",
    }).status_code == 404
    assert client.get(read_path, headers={
        "Authorization": f"Bearer {other_token}",
    }).status_code == 404
    with factory() as session:
        studies = list(session.scalars(select(EquityValidationStudy)))
        assert len(studies) == 2
        parent = next(study for study in studies if study.generation == 1)
        child = next(study for study in studies if study.generation == 2)
        assert parent.dataset_manifest["bars"] == child.dataset_manifest["bars"]
        assert list(session.scalars(select(EquityDeployment))) == []


def test_policy_trials_freeze_from_owned_study_without_touching_holdout_or_paper(
    client, setup, monkeypatch,
):
    from packages.regime.chronological_validation import review_fixed_equity_rules

    headers, body, provider, factory = setup
    long_validation_history(provider)
    request = {key: body[key] for key in ("watchlist_id", "budget", "risk_budget")}
    trial_request = {"watchlist_id": body["watchlist_id"], "strategy": "trend"}
    assert client.post("/equity/exit-policy-comparison", json=trial_request).status_code == 401
    before = client.post(
        "/equity/exit-policy-comparison", json=trial_request, headers=headers
    ).json()
    assert before["status"] == "unavailable"

    def reviewable(*args, **kwargs):
        report = review_fixed_equity_rules(*args, **kwargs)
        return report.model_copy(update={"rows": [
            report.rows[0].model_copy(update={"status": "reviewable"}),
            *report.rows[1:],
        ]})

    monkeypatch.setattr("apps.api.equity_routes.review_fixed_equity_rules", reviewable)
    frozen = client.post(
        "/equity/chronological-validation", json=request, headers=headers
    ).json()
    assert frozen["frozen"]
    first = client.post(
        "/equity/exit-policy-comparison", json=trial_request, headers=headers
    )
    assert first.status_code == 200, first.text
    report = first.json()
    assert first.headers["cache-control"] == "no-store"
    assert report["status"] == "ready" and report["trial_id"]
    assert report["dataset_digest"] == frozen["dataset_digest"]
    assert len(report["rows"]) == 3
    assert report["rows"][0]["policy_version"] == "equity-exit-v1"
    assert report["reserved_from"] == frozen["reserved_from"]
    assert report["holdout_evaluated"] is False
    assert report["ranking_changed"] is False and report["parameters_changed"] is False
    assert len(report["comparison_digest"]) == 64
    with factory() as session:
        trials = list(session.scalars(select(EquityPolicyTrial)))
        assert len(trials) == 1
        assert trials[0].trial_manifest["policies"][0]["version"] == "equity-exit-v1"
        assert list(session.scalars(select(EquityDeployment))) == []

    async def unavailable(*_args):
        raise RuntimeError("provider unavailable")

    provider.get_historical = unavailable
    second = client.post(
        "/equity/exit-policy-comparison", json=trial_request, headers=headers
    )
    assert second.status_code == 200 and second.json() == report

    other_token = client.post("/auth/register", json={
        "email": "other-policy@example.com", "password": "another-strong-password",
    }).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}
    assert client.post(
        "/equity/exit-policy-comparison", json=trial_request, headers=other_headers
    ).status_code == 404


@pytest.mark.parametrize("changed_cost", [
    "FEE", "SLIPPAGE", "legacy", "code", "legacy_code", "environment", "legacy_environment",
])
def test_policy_comparison_rejects_cost_drift(client, setup, monkeypatch, changed_cost):
    from packages.regime.chronological_validation import review_fixed_equity_rules

    headers, body, provider, factory = setup
    long_validation_history(provider)

    def reviewable(*args, **kwargs):
        report = review_fixed_equity_rules(*args, **kwargs)
        return report.model_copy(update={"rows": [
            report.rows[0].model_copy(update={"status": "reviewable"}),
            *report.rows[1:],
        ]})

    monkeypatch.setattr("apps.api.equity_routes.review_fixed_equity_rules", reviewable)
    request = {key: body[key] for key in ("watchlist_id", "budget", "risk_budget")}
    frozen = client.post("/equity/chronological-validation", json=request, headers=headers)
    assert frozen.status_code == 200 and frozen.json()["frozen"]
    with factory() as session:
        study = session.scalar(select(EquityValidationStudy))
        assert Decimal(study.dataset_manifest["fee_per_side"]) > 0
        assert Decimal(study.dataset_manifest["slippage_per_side"]) > 0
    if changed_cost in {"legacy", "legacy_code", "legacy_environment"}:
        from apps.api.equity_routes import _canonical_hash

        with factory() as session:
            study = session.scalar(select(EquityValidationStudy))
            missing = {
                "legacy": {"fee_per_side", "slippage_per_side"},
                "legacy_code": {"calculation_source_digest"},
                "legacy_environment": {"calculation_environment"},
            }[changed_cost]
            study.dataset_manifest = {
                key: value for key, value in study.dataset_manifest.items()
                if key not in missing
            }
            study.dataset_digest = _canonical_hash(study.dataset_manifest)
            study.report = {**study.report, "dataset_digest": study.dataset_digest}
            study.report_digest = _canonical_hash(study.report)
            session.commit()
    elif changed_cost == "code":
        monkeypatch.setattr("apps.api.equity_routes.research_code_digest", lambda: "0" * 64)
    elif changed_cost == "environment":
        monkeypatch.setattr(
            "apps.api.equity_routes.research_environment", lambda: {"changed": True},
        )
    else:
        monkeypatch.setattr(f"apps.api.equity_routes.{changed_cost}", Decimal("0.9"))
    result = client.post("/equity/exit-policy-comparison", headers=headers,
                         json={"watchlist_id": body["watchlist_id"], "strategy": "trend"})
    assert result.status_code == (200 if changed_cost.startswith("legacy") else 409)
    if changed_cost.startswith("legacy"):
        assert result.json()["status"] == "unavailable"
        reason = {"legacy": "fee and slippage", "legacy_code": "source identity",
                  "legacy_environment": "runtime assumptions"}[changed_cost]
        assert reason in result.json()["reason"]
    with factory() as session:
        assert list(session.scalars(select(EquityPolicyTrial))) == []
        assert list(session.scalars(select(EquityDeployment))) == []


@pytest.mark.parametrize("field", [
    "result", "fee_per_side", "slippage_per_side", "calculation_source_digest",
    "calculation_environment",
])
def test_tampered_policy_trial_is_rejected(client, setup, monkeypatch, field):
    from packages.regime.chronological_validation import review_fixed_equity_rules

    headers, body, provider, factory = setup
    long_validation_history(provider)

    def reviewable(*args, **kwargs):
        report = review_fixed_equity_rules(*args, **kwargs)
        return report.model_copy(update={"rows": [
            report.rows[0].model_copy(update={"status": "reviewable"}),
            *report.rows[1:],
        ]})

    monkeypatch.setattr("apps.api.equity_routes.review_fixed_equity_rules", reviewable)
    client.post("/equity/chronological-validation", json={
        key: body[key] for key in ("watchlist_id", "budget", "risk_budget")
    }, headers=headers)
    request = {"watchlist_id": body["watchlist_id"], "strategy": "trend"}
    assert client.post(
        "/equity/exit-policy-comparison", json=request, headers=headers
    ).status_code == 200
    with factory() as session:
        trial = session.scalar(select(EquityPolicyTrial))
        if field == "result":
            trial.result = {**trial.result, "parameters_changed": True}
        else:
            from apps.api.equity_routes import _canonical_hash

            trial.trial_manifest = {**trial.trial_manifest, field: "mismatched"}
            # A self-consistent trial hash still cannot contradict its parent study.
            trial.manifest_digest = _canonical_hash(trial.trial_manifest)
        session.commit()
    rejected = client.post(
        "/equity/exit-policy-comparison", json=request, headers=headers
    )
    assert rejected.status_code == 409
    assert ("integrity" if field == "result" else "does not match") in rejected.json()["detail"]


def test_policy_trial_migration_keeps_existing_validation_studies(tmp_path):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    from packages.database.base import Base

    path = Path("alembic/versions/0021_equity_policy_trials.py")
    spec = importlib.util.spec_from_file_location("policy_trial_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'policy-trial-migration.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        EquityPolicyTrial.__table__.drop(connection)
        original_op = migration.op
        migration.op = Operations(MigrationContext.configure(connection))
        try:
            migration.upgrade()
            assert "equity_policy_trials" in inspect(connection).get_table_names()
            assert "equity_validation_studies" in inspect(connection).get_table_names()
            migration.downgrade()
            assert "equity_policy_trials" not in inspect(connection).get_table_names()
            assert "equity_validation_studies" in inspect(connection).get_table_names()
        finally:
            migration.op = original_op
    engine.dispose()


def test_tampered_frozen_study_is_rejected(client, setup, monkeypatch):
    from packages.regime.chronological_validation import review_fixed_equity_rules

    headers, body, provider, factory = setup
    long_validation_history(provider)
    request = {key: body[key] for key in ("watchlist_id", "budget", "risk_budget")}

    def reviewable(*args, **kwargs):
        report = review_fixed_equity_rules(*args, **kwargs)
        return report.model_copy(update={"rows": [
            report.rows[0].model_copy(update={"status": "reviewable"}),
            *report.rows[1:],
        ]})

    monkeypatch.setattr("apps.api.equity_routes.review_fixed_equity_rules", reviewable)
    assert client.post(
        "/equity/chronological-validation", json=request, headers=headers
    ).status_code == 200
    with factory() as session:
        study = session.scalar(select(EquityValidationStudy))
        study.report = {**study.report, "symbol": "TAMPERED"}
        session.commit()
    rejected = client.post(
        "/equity/chronological-validation", json=request, headers=headers
    )
    assert rejected.status_code == 409
    assert "integrity" in rejected.json()["detail"]


def test_tampered_validation_dataset_is_rejected(client, setup, monkeypatch):
    from packages.regime.chronological_validation import review_fixed_equity_rules

    headers, body, provider, factory = setup
    long_validation_history(provider)
    request = {key: body[key] for key in ("watchlist_id", "budget", "risk_budget")}

    def reviewable(*args, **kwargs):
        report = review_fixed_equity_rules(*args, **kwargs)
        return report.model_copy(update={"rows": [
            report.rows[0].model_copy(update={"status": "reviewable"}),
            *report.rows[1:],
        ]})

    monkeypatch.setattr("apps.api.equity_routes.review_fixed_equity_rules", reviewable)
    assert client.post(
        "/equity/chronological-validation", json=request, headers=headers
    ).status_code == 200
    with factory() as session:
        study = session.scalar(select(EquityValidationStudy))
        manifest = dict(study.dataset_manifest)
        manifest["bars"] = [*manifest["bars"]]
        manifest["bars"][0] = [*manifest["bars"][0]]
        manifest["bars"][0][4] = "0"
        study.dataset_manifest = manifest
        session.commit()
    rejected = client.post(
        "/equity/chronological-validation", json=request, headers=headers
    )
    assert rejected.status_code == 409
    assert "dataset" in rejected.json()["detail"]


def test_visible_simulated_paper_cycle(client, setup, tmp_path):
    """Export actual disposable runtime results for the operator walkthrough."""
    import json

    headers, body, provider, factory = setup
    steps = []
    applied = client.post("/equity/deployments", json=body, headers=headers)
    assert applied.status_code == 201
    deployment_id = applied.json()["id"]

    def capture(label):
        wallet = account(client, headers)
        row = next(item for item in wallet["deployments"] if item["id"] == deployment_id)
        ledger = client.get("/equity/account/ledger-reconciliation", headers=headers).json()
        assert ledger["status"] == ledger["cash_journal"]["status"] == "MATCHED"
        steps.append({"label": label, "market_bid": str(provider.price),
                      "cash": wallet["cash"], "deployment": row, "ledger": ledger})
        return row

    capture("Applied: capital reserved")
    cycle(provider, factory)
    assert capture("Waiting: no new daily candle")["status"] == "WAITING_FOR_ENTRY"
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    cycle(provider, factory)
    opened = capture("Entry: new signal and valid quote")
    assert opened["status"] == "OPEN"
    position = opened["state"]["position"]
    provider.price = (
        Decimal(position["entry_price"]) + Decimal(position["distance"]) * Decimal("1.5")
    )
    Clock.instant += timedelta(seconds=1)
    cycle(provider, factory)
    raised = capture("Price rises: trailing stop advances")
    stop = Decimal(raised["state"]["position"]["stop"])
    assert stop > Decimal(position["entry_price"])
    provider.price = stop - Decimal("0.10")
    Clock.instant += timedelta(seconds=1)
    cycle(provider, factory)
    closed = capture("Exit: price crosses trailing stop")
    assert closed["status"] == "CLOSED"
    assert closed["state"]["exit_reason"] == "TRAILING_STOP"
    assert Decimal(steps[-1]["cash"]) == (
        Decimal("1000000") + Decimal(closed["state"]["realized_pnl"])
    )
    cycle(provider, factory)
    assert capture("Next cycle: no duplicate settlement")["state"] == closed["state"]
    assert steps[-1]["cash"] == steps[-2]["cash"]
    decisions = client.get(f"/equity/deployments/{deployment_id}/decisions", headers=headers).json()
    (tmp_path / "paper-cycle.json").write_text(json.dumps({
        "simulation": True, "scope": "Disposable database; synthetic exchange-time evidence",
        "steps": steps, "decisions": decisions,
    }, indent=2), encoding="utf-8")


def test_deploy_wait_enter_trail_close_persists_and_accounts_for_costs(client, setup):
    headers, body, provider, factory = setup
    research = {k: body[k] for k in ("watchlist_id", "budget", "risk_budget")}
    before = client.post("/equity/strategies", json=research, headers=headers).json()
    assert before["exit_policy_version"] == "equity-exit-v1"
    assert before["shared_scenario"]["exit_policy_version"] == "equity-exit-v1"
    trend_before = next(item for item in before["strategies"] if item["key"] == "trend")
    response = client.post("/equity/deployments", json=body, headers=headers)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "WAITING_FOR_ENTRY"
    assert response.json()["provider"] == provider.provider_id
    assert response.json()["instrument_key"] == "NSE_EQ|TEST"
    initial_state = response.json()["state"]
    assert Decimal(initial_state["entry_threshold_price"]) > 0
    assert initial_state["latest_daily_signal_date"] == initial_state["signal_after"]
    assert initial_state["latest_daily_signal_after_apply"] is False
    assert isinstance(initial_state["latest_daily_signal_met"], bool)
    assert account(client, headers)["cash"] == "990000.00"
    cycle(provider, factory)
    assert account(client, headers)["deployments"][0]["status"] == "WAITING_FOR_ENTRY"
    # New completed daily candle, then a later independent worker invocation enters.
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    cycle(provider, factory)
    row = account(client, headers)["deployments"][0]
    assert row["status"] == "OPEN"
    position = row["state"]["position"]
    assert position["exit_policy_version"] == "equity-exit-v1"
    assert position["exit_policy"]["target_r"] == "2"
    assert Decimal(position["stop"]) < Decimal(position["entry_price"])
    assert Decimal(position["cost"]) <= Decimal(body["budget"])
    provider.price = Decimal(position["entry_price"]) + Decimal(position["distance"]) * Decimal(
        "1.5"
    )
    Clock.instant += timedelta(seconds=1)
    cycle(provider, factory)
    row = account(client, headers)["deployments"][0]
    assert row["state"]["position"]["trailing_active"]
    stop_price = Decimal(row["state"]["position"]["stop"])
    assert stop_price > Decimal(position["entry_price"])
    provider.price = stop_price + Decimal("0.10")
    Clock.instant += timedelta(seconds=1)
    cycle(provider, factory)
    assert (
        Decimal(account(client, headers)["deployments"][0]["state"]["position"]["stop"])
        == stop_price
    )
    provider.price = stop_price - Decimal("0.10")
    Clock.instant += timedelta(seconds=1)
    cycle(provider, factory)
    result = account(client, headers)
    row = result["deployments"][0]
    assert row["status"] == "CLOSED"
    assert row["state"]["exit_reason"] == "TRAILING_STOP"
    assert Decimal(result["cash"]) == Decimal("1000000") + Decimal(row["state"]["realized_pnl"])
    assert Decimal(row["state"]["realized_pnl"]) > 0
    outcome = row["state"]["outcome_evidence"]
    assert outcome["exit_policy_version"] == position["exit_policy_version"]
    assert outcome["net_pnl"] == row["state"]["realized_pnl"]
    assert outcome["entry_market_context"]["as_of"]
    assert outcome["entry_quote_at"] and outcome["exit_quote_at"]
    assert outcome["version"] == 2
    assert outcome["entry_quote_basis"] == outcome["exit_quote_basis"] == "exchange"
    assert Decimal(outcome["entry_ask"]) == Decimal(position["reference_price"])
    assert Decimal(outcome["initial_stop"]) < Decimal(outcome["final_stop"])
    assert Decimal(outcome["initial_risk"]) > 0
    after = client.post("/equity/strategies", json=research, headers=headers).json()
    trend_after = next(item for item in after["strategies"] if item["key"] == "trend")
    assert trend_after["performance"]["paper"]["feedback"]["closed_trades"] == 1
    assert trend_after["profit_review"]["paper_feedback_trades"] == 1
    assert trend_after["profit_review"]["paper_feedback_score"] > 0
    assert trend_after["score"] > trend_before["score"]
    assert trend_after["profit_review"]["estimated_profit_probability_pct"] == (
        trend_before["profit_review"]["estimated_profit_probability_pct"]
    )
    with factory() as session:
        deployment = session.get(EquityDeployment, UUID(row["id"]))
        original_state = deployment.state
        changed = dict(original_state)
        changed["outcome_evidence"] = {
            **changed["outcome_evidence"], "exit_bid": "1.00",
        }
        deployment.state = changed
        session.commit()
    inconsistent = client.post("/equity/strategies", json=research, headers=headers).json()
    trend_inconsistent = next(
        item for item in inconsistent["strategies"] if item["key"] == "trend"
    )
    assert trend_inconsistent["performance"]["paper"]["closed_trades"] == 1
    assert trend_inconsistent["profit_review"]["paper_feedback_trades"] == 0
    with factory() as session:
        deployment = session.get(EquityDeployment, UUID(row["id"]))
        deployment.state = original_state
        session.commit()
    with factory() as session:
        exit_event = session.scalar(select(AuditEvent).where(
            AuditEvent.correlation_id == row["id"],
            AuditEvent.event_type == "equity.deployment.exited",
        ))
        original_exit_details = exit_event.details
        exit_event.details = {**original_exit_details, "bid": "1.00"}
        session.commit()
    changed_audit = client.post("/equity/strategies", json=research, headers=headers).json()
    trend_changed_audit = next(
        item for item in changed_audit["strategies"] if item["key"] == "trend"
    )
    assert trend_changed_audit["performance"]["paper"]["closed_trades"] == 1
    assert trend_changed_audit["profit_review"]["paper_feedback_trades"] == 0
    with factory() as session:
        exit_event = session.scalar(select(AuditEvent).where(
            AuditEvent.correlation_id == row["id"],
            AuditEvent.event_type == "equity.deployment.exited",
        ))
        exit_event.details = original_exit_details
        session.commit()
    cycle(provider, factory)
    assert account(client, headers)["cash"] == result["cash"]  # No duplicate settlement.
    with factory() as session:
        events = list(session.scalars(select(AuditEvent).where(
            AuditEvent.correlation_id == row["id"]
        )))
    assert sorted(event.event_type for event in events) == [
        "equity.deployment.applied",
        "equity.deployment.entered",
        "equity.deployment.entry_checked",
        "equity.deployment.exited",
        "equity.deployment.stop_raised",
    ]
    raised = next(event for event in events if event.event_type.endswith("stop_raised"))
    assert Decimal(raised.details["new_stop"]) > Decimal(raised.details["old_stop"])
    assert raised.details["quote_at"]
    exited = next(event for event in events if event.event_type.endswith("exited"))
    assert exited.details["net_pnl"] == row["state"]["realized_pnl"]
    decisions = client.get(
        f"/equity/deployments/{row['id']}/decisions", headers=headers
    )
    assert decisions.status_code == 200
    assert decisions.headers["cache-control"] == "no-store"
    assert len(decisions.json()) == 5
    assert all(item["created_at"].endswith("Z") for item in decisions.json())
    # Paper feedback must never mix outcomes from a different exit policy.
    with factory() as session:
        deployment = session.get(EquityDeployment, UUID(row["id"]))
        changed = dict(deployment.state)
        changed["outcome_evidence"] = {
            **changed["outcome_evidence"],
            "exit_policy_version": "equity-exit-research-other",
        }
        deployment.state = changed
        session.commit()
    mismatched = client.post("/equity/strategies", json=research, headers=headers).json()
    trend_mismatched = next(
        item for item in mismatched["strategies"] if item["key"] == "trend"
    )
    assert trend_mismatched["performance"]["paper"]["closed_trades"] == 1
    assert trend_mismatched["performance"]["paper"]["feedback"]["closed_trades"] == 0


def test_idempotency_conflict_symbol_guard_cancellation_and_user_isolation(client, setup):
    headers, body, provider, factory = setup
    assert client.post("/equity/deployments", json=body).status_code == 401
    first = client.post("/equity/deployments", json=body, headers=headers).json()
    provider.fail_history = True
    retry = client.post("/equity/deployments", json=body, headers=headers)
    assert retry.json()["id"] == first["id"]
    assert account(client, headers)["cash"] == "990000.00"
    changed = client.post("/equity/deployments", json={**body, "budget": "11000"}, headers=headers)
    assert changed.status_code == 409
    provider.fail_history = False
    duplicate = client.post(
        "/equity/deployments", json={**body, "command_key": str(uuid4())}, headers=headers
    )
    assert duplicate.status_code == 409
    token = client.post(
        "/auth/register",
        json={"email": "other-equity@example.com", "password": "test-strong-password"},
    ).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    assert account(client, other)["deployments"] == []
    assert client.post(f"/equity/deployments/{first['id']}/stop", headers=other).status_code == 404
    assert client.get(
        f"/equity/deployments/{first['id']}/decisions", headers=other
    ).status_code == 404
    assert client.post("/equity/deployments", json=body, headers=other).status_code == 404
    for _ in range(2):
        assert (
            client.post(f"/equity/deployments/{first['id']}/stop", headers=headers).json()["status"]
            == "CANCELLED"
        )
    assert account(client, headers)["cash"] == "1000000.00"
    with factory() as session:
        events = list(session.scalars(select(AuditEvent).where(
            AuditEvent.correlation_id == first["id"]
        )))
    assert sorted(event.event_type for event in events) == [
        "equity.deployment.applied", "equity.deployment.cancelled"
    ]


@pytest.mark.parametrize("failure", ["stale", "closed", "bad_key"])
def test_untrusted_or_closed_market_never_fills(client, setup, failure):
    headers, body, provider, factory = setup
    client.post("/equity/deployments", json=body, headers=headers)
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    setattr(provider, failure, True)
    cycle(provider, factory)
    assert account(client, headers)["deployments"][0]["status"] == "WAITING_FOR_ENTRY"
    setattr(provider, failure, False)
    cycle(provider, factory)
    assert account(client, headers)["deployments"][0]["status"] == "OPEN"
    setattr(provider, failure, True)
    provider.price = Decimal("80")
    cycle(provider, factory)
    assert account(client, headers)["deployments"][0]["status"] == "OPEN"


def test_response_timestamp_cannot_authorize_equity_entry_or_exit(client, setup):
    headers, body, provider, factory = setup
    response = client.post("/equity/deployments", json=body, headers=headers)
    assert response.status_code == 201
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    provider.quote_basis = "provider_response"
    provider.book_basis = "provider_response"
    cycle(provider, factory)
    waiting = account(client, headers)["deployments"][0]
    assert waiting["status"] == "WAITING_FOR_ENTRY"
    assert "exchange-timestamped bid/ask book" in waiting["state"]["detail"]
    assert Decimal(waiting["state"]["entry_threshold_price"]) > 0
    assert isinstance(waiting["state"]["latest_daily_signal_met"], bool)
    assert waiting["state"]["latest_daily_signal_date"]
    last_open = waiting["state"]["last_entry_check"]
    assert "exchange-timestamped bid/ask book" in last_open["detail"]
    provider.closed = True
    cycle(provider, factory)
    after_close = account(client, headers)["deployments"][0]
    assert after_close["state"]["detail"] == "Waiting for a verified open NSE session"
    assert after_close["state"]["last_entry_check"] == last_open
    provider.closed = False

    research = {key: body[key] for key in ("watchlist_id", "budget", "risk_budget")}
    result = client.post("/equity/strategies", json=research, headers=headers).json()
    assert result["live"] is False
    assert "exchange-timestamped bid/ask book" in result["live_detail"]
    assert result["shared_scenario"]["basis"] == "last_completed_close"

    provider.book_basis = "exchange"
    provider.book_lag_seconds = 120
    cycle(provider, factory)
    assert account(client, headers)["deployments"][0]["status"] == "WAITING_FOR_ENTRY"
    provider.book_lag_seconds = 0
    cycle(provider, factory)
    assert account(client, headers)["deployments"][0]["status"] == "OPEN"

    provider.price = Decimal("80")
    provider.book_basis = "provider_response"
    cycle(provider, factory)
    assert account(client, headers)["deployments"][0]["status"] == "OPEN"
    provider.book_basis = "exchange"
    cycle(provider, factory)
    assert account(client, headers)["deployments"][0]["status"] == "CLOSED"


def test_each_stale_clock_is_retained_once_in_decision_history(client, setup):
    headers, body, provider, factory = setup
    applied = client.post("/equity/deployments", json=body, headers=headers).json()
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    for response_lag, book_lag, stale_trade in ((120, 0, False), (0, 120, False), (0, 0, True)):
        provider.response_lag_seconds = response_lag
        provider.book_lag_seconds = book_lag
        provider.stale = stale_trade
        cycle(provider, factory)
        cycle(provider, factory)
    checks = [row for row in client.get(
        f"/equity/deployments/{applied['id']}/decisions", headers=headers,
    ).json() if row["event_type"] == "equity.deployment.entry_checked"]
    assert len(checks) == 3
    assert {row["details"]["code"] for row in checks} == {
        "QUOTE_TIME_STALE", "BOOK_TIME_STALE", "TRADE_TIME_STALE",
    }
    assert all(row["details"]["category"] == "DATA" for row in checks)
    assert account(client, headers)["deployments"][0]["status"] == "WAITING_FOR_ENTRY"


def test_waiting_entry_checks_are_durable_bounded_and_not_logged_after_hours(client, setup):
    headers, body, provider, factory = setup
    applied = client.post("/equity/deployments", json=body, headers=headers).json()
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    provider.book_basis = "unknown"
    cycle(provider, factory)
    cycle(provider, factory)

    url = f"/equity/deployments/{applied['id']}/decisions"
    checks = [row for row in client.get(url, headers=headers).json()
              if row["event_type"] == "equity.deployment.entry_checked"]
    assert len(checks) == 1
    assert checks[0]["details"]["reason"] == "Waiting for an exchange-timestamped bid/ask book"
    assert checks[0]["details"]["code"] == "BOOK_TIME_UNVERIFIED"
    assert checks[0]["details"]["category"] == "DATA"
    assert checks[0]["details"]["quote_mode"] == "VERIFIED"
    assert "NSE_EQ|TEST" not in str(checks[0]["details"])

    provider.book_basis = "exchange"
    provider.ask_quantity = 0
    cycle(provider, factory)
    checks = [row for row in client.get(url, headers=headers).json()
              if row["event_type"] == "equity.deployment.entry_checked"]
    assert len(checks) == 2
    assert "displayed depth" in checks[0]["details"]["reason"]
    assert checks[0]["details"]["code"] == "DEPTH_INSUFFICIENT"

    provider.closed = True
    cycle(provider, factory)
    assert len([row for row in client.get(url, headers=headers).json()
                if row["event_type"] == "equity.deployment.entry_checked"]) == 2
    waiting = account(client, headers)["deployments"][0]
    assert waiting["state"]["detail"] == "Waiting for a verified open NSE session"
    assert "displayed depth" in waiting["state"]["last_entry_check"]["detail"]


def test_strict_equity_paper_entry_and_exit_require_displayed_size(client, setup):
    headers, body, provider, factory = setup
    assert client.post("/equity/deployments", json=body, headers=headers).status_code == 201
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)

    # One share is visible, but the strategy's sized entry needs more than one.
    provider.ask_quantity = 1
    cycle(provider, factory)
    waiting = account(client, headers)["deployments"][0]
    assert waiting["status"] == "WAITING_FOR_ENTRY"
    assert "enough displayed depth" in waiting["state"]["detail"]
    assert "enough displayed depth" in waiting["state"]["last_entry_check"]["detail"]
    provider.ask_quantity = 1000
    cycle(provider, factory)
    opened = account(client, headers)["deployments"][0]
    assert opened["status"] == "OPEN"
    assert opened["state"]["quote_mode"] == "VERIFIED"

    # A stop signal cannot close against a bid with no displayed quantity.
    provider.price = Decimal("80")
    provider.bid_quantity = 0
    cycle(provider, factory)
    blocked = account(client, headers)["deployments"][0]
    assert blocked["status"] == "OPEN"
    assert "enough displayed depth" in blocked["state"]["detail"]
    provider.bid_quantity = 1000
    cycle(provider, factory)
    assert account(client, headers)["deployments"][0]["status"] == "CLOSED"


def test_opt_in_indicative_equity_paper_cycle_is_descriptive_only(client, setup):
    headers, body, provider, factory = setup
    body["indicative_quotes"] = True
    applied = client.post("/equity/deployments", json=body, headers=headers)
    assert applied.status_code == 201
    assert applied.json()["state"]["quote_mode"] == "INDICATIVE"
    provider.quote_basis = "provider_response"
    provider.book_basis = "unknown"
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    provider.ask_quantity = 0
    cycle(provider, factory)
    assert account(client, headers)["deployments"][0]["status"] == "WAITING_FOR_ENTRY"
    provider.ask_quantity = 1000
    cycle(provider, factory)
    opened = account(client, headers)["deployments"][0]
    assert opened["status"] == "OPEN"
    assert opened["state"]["entry_quote_basis"] == "provider_response"
    assert opened["state"]["entry_book_as_of"] is None
    assert "Indicative" in opened["state"]["detail"]
    provider.price = Decimal("80")
    provider.bid_quantity = 0
    cycle(provider, factory)
    assert account(client, headers)["deployments"][0]["status"] == "OPEN"
    provider.bid_quantity = 1000
    cycle(provider, factory)
    closed = account(client, headers)["deployments"][0]
    assert closed["status"] == "CLOSED"
    assert closed["state"]["outcome_evidence"]["exit_quote_basis"] == "provider_response"
    assert closed["state"]["outcome_evidence"]["exit_book_as_of"] is None
    assert Decimal(closed["state"]["realized_pnl"]) < 0
    research = {key: body[key] for key in ("watchlist_id", "budget", "risk_budget")}
    ranked = client.post("/equity/strategies", json=research, headers=headers).json()
    trend = next(item for item in ranked["strategies"] if item["key"] == "trend")
    assert trend["performance"]["paper"]["closed_trades"] == 1
    assert trend["profit_review"]["paper_feedback_trades"] == 0


def test_indicative_equity_quote_rejects_unknown_time_and_old_response(client, setup):
    headers, body, provider, factory = setup
    body["indicative_quotes"] = True
    assert client.post("/equity/deployments", json=body, headers=headers).status_code == 201
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    provider.book_basis = "unknown"
    provider.quote_basis = "api_response_received"
    cycle(provider, factory)
    waiting = account(client, headers)["deployments"][0]
    assert waiting["status"] == "WAITING_FOR_ENTRY"
    assert "provider response timestamp" in waiting["state"]["detail"]
    provider.quote_basis = "provider_response"
    provider.response_lag_seconds = 8
    cycle(provider, factory)
    waiting = account(client, headers)["deployments"][0]
    assert waiting["status"] == "WAITING_FOR_ENTRY"
    assert "within 5 seconds" in waiting["state"]["detail"]


def test_invalid_saved_position_quantity_cannot_make_paper_fill(client, setup):
    headers, body, provider, factory = setup
    body["indicative_quotes"] = True
    assert client.post("/equity/deployments", json=body, headers=headers).status_code == 201
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    provider.book_basis = "unknown"
    provider.quote_basis = "provider_response"
    cycle(provider, factory)
    with factory() as session:
        row = session.scalar(select(EquityDeployment))
        assert row is not None and row.status == "OPEN"
        state = dict(row.state)
        state["position"] = {**state["position"], "quantity": "invalid"}
        row.state = state
        session.commit()
    provider.price = Decimal("80")
    cycle(provider, factory)
    saved = account(client, headers)["deployments"][0]
    assert saved["status"] == "OPEN"
    assert "invalid quantity" in saved["state"]["detail"]


def test_manual_exit_waits_for_fresh_quote_and_history_failure_cannot_disable_stops(client, setup):
    headers, body, provider, factory = setup
    row = client.post("/equity/deployments", json=body, headers=headers).json()
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    cycle(provider, factory)
    provider.fail_history = True
    provider.stale = True
    assert (
        client.post(f"/equity/deployments/{row['id']}/stop", headers=headers).json()["status"]
        == "EXIT_REQUESTED"
    )
    cycle(provider, factory)
    assert account(client, headers)["deployments"][0]["status"] == "EXIT_REQUESTED"
    provider.stale = False
    cycle(provider, factory)
    assert account(client, headers)["deployments"][0]["state"]["exit_reason"] == "MANUAL_EXIT"
    provider.fail_history = False
    research = {key: body[key] for key in ("watchlist_id", "budget", "risk_budget")}
    ranked = client.post("/equity/strategies", json=research, headers=headers).json()
    trend = next(item for item in ranked["strategies"] if item["key"] == "trend")
    assert trend["performance"]["paper"]["closed_trades"] == 1
    assert trend["profit_review"]["paper_feedback_trades"] == 0


def test_expiry_releases_reserved_cash_even_when_market_closed(client, setup):
    headers, body, provider, factory = setup
    client.post("/equity/deployments", json=body, headers=headers)
    provider.closed = True
    Clock.instant += timedelta(days=8)
    cycle(provider, factory)
    result = account(client, headers)
    assert result["deployments"][0]["status"] == "EXPIRED"
    assert result["cash"] == "1000000.00"
    cycle(provider, factory)
    with factory() as session:
        events = list(session.scalars(select(AuditEvent).where(
            AuditEvent.correlation_id == result["deployments"][0]["id"]
        )))
    assert sorted(event.event_type for event in events) == [
        "equity.deployment.applied", "equity.deployment.expired"
    ]
    expired = next(event for event in events if event.event_type.endswith("expired"))
    assert expired.details["reserved_cash_released"] == "10000.00"


def test_research_five_honest_tiles_and_provider_failure_sanitized(client, setup):
    headers, body, provider, factory = setup
    research = {k: body[k] for k in ("watchlist_id", "budget", "risk_budget")}
    response = client.post("/equity/strategies", json=research, headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["strategies"]) == 10
    assert data["live"]
    assert any(row["signal"] for row in data["strategies"])
    assert data["top_three"] == [row["key"] for row in data["strategies"][:3]]
    assert data["ranking_version"] == "profit-review-v6"
    review = data["strategies"][0]["profit_review"]
    assert "estimated_profit_probability_pct" in review
    assert review["probability_confidence"] in {"unavailable", "low", "medium", "high"}
    assert review["adaptive_parameter_note"]
    assert data["calculation_version"] == "equity-calculations-v2"
    assert data["shared_scenario"]["basis"] == "live_ask"
    assert Decimal(data["shared_scenario"]["reference_price"]) == provider.price + Decimal('.10')
    for row in data["strategies"]:
        assert row["proposed"] == (
            data["shared_scenario"]["position"] if row["history_ready"] else None
        )
        assert row["entry_criteria"]
        if not row["evidence"]["closed_trades"]:
            assert row["evidence"]["win_rate"] is None
            assert row["evidence"]["average_net_pnl"] is None
    provider.closed = True
    historical = client.post("/equity/strategies", json=research, headers=headers).json()
    assert not historical["live"]
    assert historical["shared_scenario"]["basis"] == "last_completed_close"
    assert Decimal(historical["shared_scenario"]["reference_price"]) == Decimal(160)
    provider.fail_history = True
    failed = client.post("/equity/strategies", json=research, headers=headers)
    assert failed.status_code == 503
    assert "secret-provider-token" not in failed.text


def test_closed_paper_results_remain_separate_and_do_not_change_ranking(client, setup):
    headers, body, provider, factory = setup
    research = {k: body[k] for k in ("watchlist_id", "budget", "risk_budget")}
    before = client.post("/equity/strategies", json=research, headers=headers).json()
    trend_before = next(row for row in before["strategies"] if row["key"] == "trend")

    with factory() as session:
        user = session.scalar(select(User).where(User.email == "equity@example.com"))
        session.add(EquityDeployment(
            user_id=user.id,
            command_key=str(uuid4()),
            request_hash="closed-paper-test",
            provider=provider.provider_id,
            instrument_key="NSE_EQ|TEST",
            symbol="TEST",
            strategy="trend",
            version="equity-daily-v1",
            budget=Decimal(body["budget"]),
            risk_budget=Decimal(body["risk_budget"]),
            status="CLOSED",
            state={
                "realized_pnl": "123.45",
                "exit_reason": "PROFIT_TARGET",
                "detail": "Closed during live-paper validation",
            },
            created_at=Clock.instant - timedelta(days=1),
            updated_at=Clock.instant,
        ))
        session.commit()

    after = client.post("/equity/strategies", json=research, headers=headers).json()
    trend_after = next(row for row in after["strategies"] if row["key"] == "trend")
    assert trend_after["evidence"]["paper_closed_trades"] == 1
    assert trend_after["evidence"]["paper_net_pnl"] == 123.45
    assert trend_after["evidence"]["source"] == "historical_replay"
    assert trend_after["performance"]["historical"] == trend_before["performance"]["historical"]
    paper = trend_after["performance"]["paper"]
    assert paper["closed_trades"] == 1
    assert paper["net_pnl"] == 123.45
    assert paper["win_rate"] == 100
    assert paper["average_r"] is None  # Legacy record has no original risk.
    assert paper["profit_factor"] is None and paper["profit_factor_status"] == "no_losses"
    assert paper["fallback_exit_dates"] == 1
    assert trend_after["score"] == trend_before["score"]
    assert after["top_three"] == before["top_three"]



def test_new_rule_with_missing_history_cannot_reserve_cash(client, setup):
    headers, body, _, _ = setup
    result = client.post('/equity/deployments', headers=headers,
                         json={**body, 'strategy': 'connors_r2'})
    assert result.status_code == 422 and '200 completed bars' in result.text
    assert account(client, headers)['cash'] == '1000000'


def test_paper_report_uses_original_risk_and_exit_order_and_isolates_records(client, setup):
    headers, body, provider, factory = setup
    with factory() as session:
        user = session.scalar(select(User).where(User.email == "equity@example.com"))
        other = User(email="other-performance@example.com", password_hash="unused")
        session.add(other)
        session.flush()

        def add(pnl, day, risk, **overrides):
            stamp = Clock.instant + timedelta(days=day)
            values = dict(
                user_id=user.id, command_key=str(uuid4()), request_hash="report-fixture",
                provider=provider.provider_id, instrument_key="NSE_EQ|TEST", symbol="TEST",
                strategy="trend", version="equity-daily-v1", budget=Decimal("10000"),
                risk_budget=Decimal("200"), status="CLOSED",
                created_at=Clock.instant - timedelta(days=10),
                # Saved order deliberately differs from actual exit order.
                updated_at=Clock.instant - timedelta(days=day),
                state={"realized_pnl": str(pnl), "exit_at": stamp.isoformat(), "position": {
                    "calculation_version": "equity-calculations-v2",
                    "stop_scenario_net": str(-risk), "stop": "9999", "trailing_active": True,
                }},
            )
            values.update(overrides)
            session.add(EquityDeployment(**values))

        add(100, 1, 50)
        add(-50, 2, 50)
        add(-75, 3, 150)
        add(10000, 1, 50, user_id=other.id)
        add(10000, 1, 50, instrument_key="NSE_EQ|OTHER")
        add(10000, 1, 50, provider="other-provider")
        add(10000, 1, 50, strategy="breakout")
        add(10000, 1, 50, status="CANCELLED")
        add(10000, 1, 50, status="OPEN")
        add("NaN", 1, 50)
        session.commit()
    request = {k: body[k] for k in ("watchlist_id", "budget", "risk_budget")}
    response = client.post("/equity/strategies", json=request, headers=headers)
    assert response.status_code == 200, response.text
    trend = next(row for row in response.json()["strategies"] if row["key"] == "trend")
    paper = trend["performance"]["paper"]
    assert paper["closed_trades"] == 3
    assert paper["net_pnl"] == -25
    assert paper["average_r"] == pytest.approx(1 / 6)
    assert paper["closed_trade_drawdown"] == 125
    assert paper["closed_trade_drawdown_r"] == 1.5
    assert paper["profit_factor"] == .8
    assert paper["open_trades_excluded"] == 1
    assert paper["invalid_records_excluded"] == 1
    # Editing the research allocation must not rewrite recorded paper outcomes.
    changed = client.post("/equity/strategies", headers=headers,
                          json={**request, "budget": "20000", "risk_budget": "400"}).json()
    assert next(row for row in changed["strategies"] if row["key"] == "trend")[
        "performance"]["paper"] == paper


@pytest.mark.parametrize('key,closes', [
    ('channel20', [100] * 50 + [102]),
    ('channel55', [100] * 55 + [102]),
    ('connors_r2', [100] * 190 + [120, 121, 122, 123, 124, 125, 126, 125, 124, 123]),
    ('band_reclaim', [100] * 49 + [80, 90]),
    ('sma_cross', [100] * 50 + [110]),
])
def test_new_adaptation_apply_worker_entry_and_stop(client, setup, monkeypatch, key, closes):
    headers, body, provider, factory = setup

    async def bars(*args):
        return [HistoricalBar(
            timestamp=provider.day - timedelta(days=len(closes) - 1 - i),
            open=close, high=close + 1, low=close - 1, close=close, volume=10000,
        ) for i, close in enumerate(closes)]

    monkeypatch.setattr(provider, 'get_historical', bars)
    provider.price = Decimal(closes[-1])
    response = client.post('/equity/deployments', headers=headers, json={**body, 'strategy': key})
    assert response.status_code == 201
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    cycle(provider, factory)
    row = account(client, headers)['deployments'][0]
    assert row['status'] == 'OPEN' and row['strategy'] == key
    p = row['state']['position']
    assert p['calculation_version'] == 'equity-calculations-v2'
    assert -Decimal(p['stop_scenario_net']) <= Decimal(body['risk_budget'])
    provider.price = Decimal(p['stop'])
    cycle(provider, factory)
    row = account(client, headers)['deployments'][0]
    assert row['status'] == 'CLOSED' and row['state']['exit_reason'] == 'STOP_LOSS'
    assert Decimal(row['state']['realized_pnl']) == Decimal(p['stop_scenario_net'])


def test_capital_validation_provider_mismatch_and_disabled_trading(client, setup, monkeypatch):
    headers, body, provider, factory = setup
    for extra in (
        {"budget": "1000000"},
        {"risk_budget": "1001"},
        {"budget": "100", "risk_budget": "100"},
        {"strategy": "unknown"},
    ):
        assert (
            client.post("/equity/deployments", json={**body, **extra}, headers=headers).status_code
            == 422
        )
    provider.provider_id = "changed"
    assert client.post("/equity/deployments", json=body, headers=headers).status_code == 409
    provider.provider_id = "test-provider"
    from apps.api.settings import get_settings

    monkeypatch.setattr(get_settings(), "paper_trading_enabled", False)
    assert client.post("/equity/deployments", json=body, headers=headers).status_code == 403


def test_no_lookahead_and_trailing_stop_never_loosens():
    provider = Provider()
    bars = asyncio.run(provider.get_historical("key", None, None, "1d"))
    assert signal("trend", bars)
    assert not signal("trend", bars[:50])
    partial = bars[-1].model_copy(update={"timestamp": Clock.instant, "close": Decimal("100000")})
    assert completed_bars([*bars, partial], Clock.instant) == bars
    with pytest.raises(ValueError):
        completed_bars([*bars, bars[-1]], Clock.instant)
    position = open_position(Decimal("100"), Decimal("5"), Decimal("1000"), Decimal("50"))
    assert position
    moved = trail(position, Decimal("107"))
    assert moved["trailing_active"]
    assert trail(moved, Decimal("103"))["stop"] == moved["stop"]
    assert exit_reason(moved, Decimal("101")) == "TRAILING_STOP"
    # Both target and stop touched: conservative replay settles the stop first.
    bar = bars[-1].model_copy(
        update={
            "timestamp": bars[-1].timestamp + timedelta(days=1),
            "open": Decimal("160"),
            "high": Decimal("190"),
            "low": Decimal("140"),
            "close": Decimal("160"),
        }
    )
    result = replay("trend", [*bars[:51], bar], Decimal("10000"), Decimal("200"))
    # Gap over 2% is not chased.
    assert result["closed_trades"] == 0
    bar = bar.model_copy(update={"open": Decimal("151"), "close": Decimal("151")})
    result = replay("trend", [*bars[:51], bar], Decimal("10000"), Decimal("200"))
    assert result["closed_trades"] == 1
    assert result["net_pnl"] < 0


def test_cancelled_deployment_is_not_resurrected_by_worker(client, setup):
    headers, body, provider, factory = setup
    row = client.post("/equity/deployments", json=body, headers=headers).json()
    client.post(f"/equity/deployments/{row['id']}/stop", headers=headers)
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    cycle(provider, factory)
    with factory() as session:
        persisted = session.get(EquityDeployment, UUID(row["id"]))
        assert persisted.status == "CANCELLED"
    assert account(client, headers)["cash"] == "1000000.00"


@pytest.mark.parametrize("price,reason", [("140", "STOP_LOSS"), ("180", "PROFIT_TARGET")])
def test_protective_stop_and_target_exit_without_history(client, setup, price, reason):
    headers, body, provider, factory = setup
    client.post("/equity/deployments", json=body, headers=headers)
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    cycle(provider, factory)
    provider.fail_history = True
    provider.price = Decimal(price)
    cycle(provider, factory)
    row = account(client, headers)["deployments"][0]
    assert row["status"] == "CLOSED"
    assert row["state"]["exit_reason"] == reason
    if reason == "STOP_LOSS":
        # A gap is filled at the observed bid, not an invented fill at the stop price.
        assert Decimal(row["state"]["exit_price"]) < Decimal(row["state"]["position"]["stop"])


def test_expired_worker_cannot_enter(client, setup):
    from packages.paper_trading.worker_lease import LeaseToken

    headers, body, provider, factory = setup
    client.post("/equity/deployments", json=body, headers=headers)
    Clock.instant += timedelta(days=1)
    provider.day += timedelta(days=1)
    result = asyncio.run(process_equities(factory, provider, LeaseToken("old-owner", -1)))
    assert result[2] == ["Equity worker lease expired"]
    assert account(client, headers)["deployments"][0]["status"] == "WAITING_FOR_ENTRY"


def test_shared_worker_status_does_not_expose_another_accounts_warning(client, setup):
    from packages.database.models import WorkerLease

    headers, _, _, factory = setup
    with factory() as session:
        token = acquire(session, "privacy-check", 120)
        finish(session, token, "DEGRADED", "Other account position TEST-PRIVATE expired")
    result = client.get("/paper/worker", headers=headers).json()
    assert result["status"] == "DEGRADED"
    assert "TEST-PRIVATE" not in result["detail"]
    with factory() as session:
        assert "TEST-PRIVATE" in session.get(WorkerLease, "paper-exits").detail


def test_concurrent_retries_reserve_capital_once(setup, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from apps.api.equity_routes import DeployRequest, deploy
    from packages.database.base import Base
    from packages.database.models import EquityAccount

    _, body, _, _ = setup
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-equity.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        user = User(email="race@example.com", password_hash="unused-test-hash")
        session.add(user)
        session.flush()
        session.add(
            WatchlistItem(
                id=UUID(body["watchlist_id"]),
                user_id=user.id,
                provider="test-provider",
                instrument_key="NSE_EQ|TEST",
                trading_symbol="TEST",
                exchange="NSE",
                segment="NSE_EQ",
                instrument_type="EQ",
            )
        )
        session.commit()

    def submit():
        with factory() as session:
            return asyncio.run(deploy(DeployRequest(**body), user, session))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: submit(), range(2)))
    assert results[0]["id"] == results[1]["id"]
    with factory() as session:
        assert len(list(session.scalars(select(EquityDeployment)))) == 1
        assert session.get(EquityAccount, user.id).cash == Decimal("990000")
    engine.dispose()


def test_equity_migration_preserves_existing_tables(tmp_path):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    from packages.database.base import Base

    spec = importlib.util.spec_from_file_location(
        "equity_migration", Path("alembic/versions/0013_equity_deployments.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    # Start with the previously shipped schema; migration adds only its own tables.
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        EquityDeployment.__table__.drop(connection)
        from packages.database.models import EquityAccount

        EquityAccount.__table__.drop(connection)
        original_op = module.op
        module.op = Operations(MigrationContext.configure(connection))
        try:
            module.upgrade()
            assert {"users", "watchlist_items", "equity_accounts", "equity_deployments"} <= set(
                inspect(connection).get_table_names()
            )
            module.downgrade()
            assert "equity_deployments" not in inspect(connection).get_table_names()
            assert "watchlist_items" in inspect(connection).get_table_names()
        finally:
            module.op = original_op
    engine.dispose()


def test_equity_validation_study_migration_preserves_existing_tables(tmp_path):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    from packages.database.base import Base

    spec = importlib.util.spec_from_file_location(
        "validation_migration", Path("alembic/versions/0018_equity_validation_studies.py")
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'validation-migration.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        EquityValidationStudy.__table__.drop(connection)
        original_op = migration.op
        migration.op = Operations(MigrationContext.configure(connection))
        try:
            migration.upgrade()
            names = set(inspect(connection).get_table_names())
            assert "equity_validation_studies" in names
            assert "equity_deployments" in names
            columns = {
                column["name"] for column in inspect(connection).get_columns(
                    "equity_validation_studies"
                )
            }
            assert {"dataset_digest", "report_digest", "report", "frozen_at"} <= columns
            migration.downgrade()
            assert "equity_validation_studies" not in inspect(connection).get_table_names()
            assert "equity_deployments" in inspect(connection).get_table_names()
        finally:
            migration.op = original_op
    engine.dispose()


def test_equity_validation_dataset_migration_preserves_studies(tmp_path):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    from packages.database.base import Base

    spec = importlib.util.spec_from_file_location(
        "dataset_migration", Path("alembic/versions/0019_equity_validation_dataset.py")
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'dataset-migration.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE equity_validation_studies DROP COLUMN dataset_manifest"
        ))
        original_op = migration.op
        migration.op = Operations(MigrationContext.configure(connection))
        try:
            migration.upgrade()
            columns = {column["name"] for column in inspect(connection).get_columns(
                "equity_validation_studies"
            )}
            assert "dataset_manifest" in columns
            assert "report" in columns
            migration.downgrade()
            columns = {column["name"] for column in inspect(connection).get_columns(
                "equity_validation_studies"
            )}
            assert "dataset_manifest" not in columns
            assert "report" in columns
        finally:
            migration.op = original_op
    engine.dispose()

