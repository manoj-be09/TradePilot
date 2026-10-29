import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

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


def test_bollinger_reference_is_not_a_complete_entry_signal():
    provider = Provider()
    bars = asyncio.run(provider.get_historical(
        "NSE_EQ|TEST", provider.day - timedelta(days=60), provider.day, "1d",
    ))
    assert bars[-1].close > entry_threshold("band_reclaim", bars)
    assert signal("band_reclaim", bars) is False


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

