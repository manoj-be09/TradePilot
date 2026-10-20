from datetime import datetime, timedelta, timezone

from apps.api import main
from packages.market_data.contracts import OptionChain
from packages.options.market_fit import MarketFitAssessment
from packages.regime.candidate_registry import fingerprint


def test_opportunity_http_requires_owner_and_retains_one_abstention(client, monkeypatch):
    owner = client.post("/auth/register", json={
        "email": "opportunity-owner@example.org", "password": "a-strong-test-password",
    })
    other = client.post("/auth/register", json={
        "email": "opportunity-other@example.org", "password": "a-strong-test-password",
    })
    assert owner.status_code == other.status_code == 201
    owner_headers = {"Authorization": f"Bearer {owner.json()['access_token']}"}
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    study = client.post("/research/studies", headers=owner_headers, json={
        "study_key": "nifty-prospective-v1",
        "objective": "Observe market-fit abstentions before any policy result is known.",
        "incumbent_rule_version": "nifty-paper-v1",
        "dataset_digest": "a" * 64, "calculation_digest": "b" * 64,
        "cost_model_id": "illustrative-v1", "capital": "100000.00",
        "parent_search_budget": 10, "review_interval_days": 30,
        "uncertainty_note": "Indicative evidence only; book timing and costs are unreviewed.",
        "train_start": "2020-01-01", "train_end": "2022-12-31",
        "validation_start": "2023-01-01", "validation_end": "2024-12-31",
        "holdout_start": "2025-01-01", "holdout_end": "2025-12-31",
    })
    assert study.status_code == 200
    policy_path = f"/research/studies/{study.json()['id']}/policy-set"
    policy = client.post(policy_path, headers=owner_headers, json={
        "incumbent": {
            "strategy_key": "bull_call_spread", "strategy_version": "1.0.0",
            "policy_version": "nifty-paper-v1",
            "policy_artifact_digest": "c" * 64,
            "entry_rule": "Use only the predeclared completed-bar entry window.",
            "exit_rule": "Use only the predeclared stop, target and time exit.",
            "risk_rule": "Abstain when the basket, costs or capital are unavailable.",
        },
        "challengers": [], "limitation": "Research only; no qualified costs or fills.",
    })
    assert policy.status_code == 200
    assert client.get(policy_path, headers=other_headers).status_code == 404
    assert client.get(policy_path, headers=owner_headers).json()["id"] == policy.json()["id"]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    body = {"reference_strike": "100", "chain": {
        "underlying": "NIFTY", "underlying_spot": "100",
        "expiry": (now + timedelta(days=7)).date().isoformat(),
        "as_of": now.isoformat(), "contracts": [],
    }}

    async def assess(_user, _body):
        return MarketFitAssessment(
            chain_as_of=now,
            chain_digest=fingerprint(_body.chain.model_dump(mode="json")),
            reference_strike=_body.reference_strike,
            regime_as_of=None, regime="UNKNOWN",
            reasons=["No qualified policy evidence."], candidates=[],
        )

    monkeypatch.setattr(main, "analytics_options_market_fit", assess)
    path = f"/research/studies/{study.json()['id']}/opportunities/market-fit"
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, headers=other_headers, json=body).status_code == 404
    first = client.post(path, headers=owner_headers, json=body)
    second = client.post(path, headers=owner_headers, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["decision"] == "ABSTAIN"
    assert first.json()["snapshot"]["evidence_snapshot"]["chain_source"] == (
        "CLIENT_SUBMITTED_CHAIN")
    spoofed = client.post(f"{path}?chain_provider_id=spoofed", headers=owner_headers,
                          json=body)
    assert spoofed.json()["snapshot"]["evidence_snapshot"]["chain_source"] == (
        "CLIENT_SUBMITTED_CHAIN")
    assert first.json()["snapshot"]["evidence_snapshot"]["policy_set_digest"] == (
        policy.json()["manifest_digest"])
    rows = client.get(
        f"/research/studies/{study.json()['id']}/recommendations", headers=owner_headers,
    )
    assert rows.status_code == 200
    assert len(rows.json()) == 1
    assert rows.json()[0]["execution_enabled"] is False
    event_path = f"/research/opportunities/{first.json()['id']}/events"
    event = {
        "event_key": "checkpoint-1", "status": "UNRESOLVED",
        "reason_code": "OUTCOME_PENDING", "observed_at": now.isoformat(),
        "available_at": now.isoformat(), "source_artifact_digest": "e" * 64,
    }
    assert client.post(event_path, json=event).status_code == 401
    assert client.post(event_path, headers=other_headers, json=event).status_code == 404
    first_event = client.post(event_path, headers=owner_headers, json=event)
    second_event = client.post(event_path, headers=owner_headers, json=event)
    assert first_event.status_code == second_event.status_code == 200
    assert first_event.json()["id"] == second_event.json()["id"]
    assert first_event.json()["evidence_qualification"] == "UNVERIFIED_SHADOW"
    assert len(client.get(event_path, headers=owner_headers).json()) == 1

    class Provider:
        provider_id = "test-provider"
        calls = 0

        async def get_option_chain(self, underlying, expiry):
            Provider.calls += 1
            assert underlying == "NIFTY"
            assert expiry.isoformat() == body["chain"]["expiry"]
            return OptionChain.model_validate(body["chain"])

    monkeypatch.setattr(main, "get_market_data_provider", Provider)
    provider_path = f"{path}/provider"
    provider_body = {
        "expiry": body["chain"]["expiry"], "reference_strike": "100",
    }
    assert client.post(provider_path, json=provider_body).status_code == 401
    assert client.post(provider_path, headers=other_headers,
                       json=provider_body).status_code == 404
    assert Provider.calls == 0
    fetched = client.post(provider_path, headers=owner_headers, json=provider_body)
    assert fetched.status_code == 200, fetched.text
    assert Provider.calls == 1
    evidence = fetched.json()["snapshot"]["evidence_snapshot"]
    assert evidence["chain_source"] == "SERVER_FETCHED_OPTION_CHAIN"
    assert evidence["chain_provider_id"] == "test-provider"
    assert evidence["chain_artifact_review"] == "UNREVIEWED_SERVER_FETCHED"
    assert evidence["point_in_time_availability_review"] == "UNVERIFIED"
    assert fetched.json()["decision"] == "ABSTAIN"
    assert fetched.json()["execution_enabled"] is False
    assert len(client.get(f"/research/studies/{study.json()['id']}/recommendations",
                          headers=owner_headers).json()) == 2

    class WrongExpiryProvider(Provider):
        async def get_option_chain(self, underlying, expiry):
            chain = await super().get_option_chain(underlying, expiry)
            return chain.model_copy(update={"expiry": expiry + timedelta(days=1)})

    monkeypatch.setattr(main, "get_market_data_provider", WrongExpiryProvider)
    assert client.post(provider_path, headers=owner_headers,
                       json=provider_body).status_code == 409
