from datetime import datetime, timedelta, timezone

from apps.api import main
from packages.options.market_fit import MarketFitAssessment


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
    now = datetime.now(timezone.utc).replace(microsecond=0)
    body = {"reference_strike": "100", "chain": {
        "underlying": "NIFTY", "underlying_spot": "100",
        "expiry": (now + timedelta(days=7)).date().isoformat(),
        "as_of": now.isoformat(), "contracts": [],
    }}

    async def assess(_user, _body):
        return MarketFitAssessment(
            chain_as_of=now, regime_as_of=None, regime="UNKNOWN",
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
    rows = client.get(
        f"/research/studies/{study.json()['id']}/recommendations", headers=owner_headers,
    )
    assert rows.status_code == 200
    assert len(rows.json()) == 1
    assert rows.json()[0]["execution_enabled"] is False
