import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.research_routes import (
    ManualAbstentionRequest,
    create_recommendation,
    recommendations,
)
from packages.database.base import Base
from packages.database.models import ResearchRecommendation, ResearchStudy, User
from packages.market_data.contracts import OptionChain
from packages.options.market_fit import (
    MarketFitAssessment,
    MarketFitCandidate,
    MarketFitLeg,
    MarketFitRequest,
)
from packages.regime.candidate_registry import (
    CandidateDefinition,
    TrialManifest,
    declare_trial,
    fingerprint,
    register_candidate,
)
from packages.regime.frozen_nifty_study import FrozenNiftyStudy
from packages.regime.research_policy_sets import FrozenPolicySet, freeze_policy_set
from packages.regime.research_readiness import ReadinessAssessment, ReadinessCheck
from packages.regime.research_recommendations import (
    RecommendationSnapshot,
    build_abstention_recommendation,
    build_market_fit_opportunity,
    record_recommendation,
)


def study(**changes):
    return FrozenNiftyStudy(**({
        "study_key": "nifty50-paper-v1",
        "objective": "Compare the incumbent rule with a no-trade comparator.",
        "incumbent_rule_version": "nifty-paper-v1", "dataset_digest": "a" * 64,
        "calculation_digest": "b" * 64, "cost_model_id": "illustrative-v1",
        "capital": "100000.00", "parent_search_budget": 10,
        "review_interval_days": 30,
        "uncertainty_note": "Indicative only until source and fill evidence are reviewed.",
        "train_start": "2020-01-01", "train_end": "2022-12-31",
        "validation_start": "2023-01-01", "validation_end": "2024-12-31",
        "holdout_start": "2025-01-01", "holdout_end": "2025-12-31",
    } | changes))


def snapshot(**changes):
    return RecommendationSnapshot(**({
        "decision": "ABSTAIN", "reason_code": "INSUFFICIENT_EVIDENCE",
        "selector_version": "selector-v1", "evidence_snapshot": {
            "data": "UNKNOWN", "costs": "UNKNOWN",
        }, "rejected_reasons": ["No reviewed holdout evidence."],
        "risk_context": {"execution": "disabled"},
        "expires_at": "2026-02-01T10:00:00+05:30",
    } | changes))


def _fixture():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner, other = uuid4(), uuid4()
    with Session(engine) as session:
        session.add_all([
            User(id=owner, email="recommend-one@example.org", password_hash="fixture"),
            User(id=other, email="recommend-two@example.org", password_hash="fixture"),
        ])
        session.commit()
        saved = ResearchStudy(
            id=uuid4(), user_id=owner, study_key=study().study_key,
            manifest=study().manifest,
            manifest_digest=study().manifest_digest,
            created_at=datetime.now(timezone.utc),
        )
        session.add(saved)
        session.commit()
        return engine, owner, other, saved.id


def test_recommendation_is_account_scoped_idempotent_and_non_executable():
    engine, owner, other, study_id = _fixture()
    with Session(engine) as session:
        first = record_recommendation(session, owner, study_id, snapshot())
        assert record_recommendation(session, owner, study_id, snapshot()).id == first.id
        rows = recommendations(study_id, SimpleNamespace(id=owner), session,
                                SimpleNamespace(headers={}), 25, 0)
        assert rows[0]["decision"] == "ABSTAIN"
        assert rows[0]["snapshot"]["expires_at"] == "2026-02-01T04:30:00Z"
        assert len(list(session.scalars(select(ResearchRecommendation)))) == 1
        with pytest.raises(HTTPException) as error:
            recommendations(study_id, SimpleNamespace(id=other), session,
                            SimpleNamespace(headers={}), 25, 0)
        assert error.value.status_code == 404
    engine.dispose()


def test_manual_abstention_is_server_labeled_unreviewed_and_bounded():
    engine, owner, other, study_id = _fixture()
    expiry = datetime.now(timezone.utc) + timedelta(days=1)
    request = ManualAbstentionRequest(expires_at=expiry)
    with Session(engine) as session:
        view = create_recommendation(study_id, request, SimpleNamespace(id=owner), session)
        replay = create_recommendation(study_id, request, SimpleNamespace(id=owner), session)
        assert replay["id"] == view["id"]
        assert view["decision"] == "ABSTAIN"
        assert view["execution_enabled"] is False
        assert view["promotion_eligible"] is False
        assert view["snapshot"]["selector_version"] == "manual-abstention-v1"
        assert view["snapshot"]["evidence_snapshot"]["evidence_status"] == "UNREVIEWED"
        assert view["snapshot"]["evidence_snapshot"]["study_manifest_digest"] == (
            study().manifest_digest)
        with pytest.raises(HTTPException) as error:
            create_recommendation(study_id, request, SimpleNamespace(id=other), session)
        assert error.value.status_code == 404
        with pytest.raises(HTTPException) as error:
            create_recommendation(study_id, ManualAbstentionRequest(
                expires_at=expiry + timedelta(days=31)), SimpleNamespace(id=owner), session)
        assert error.value.status_code == 422
        assert len(list(session.scalars(select(ResearchRecommendation)))) == 1
    with pytest.raises(ValidationError):
        ManualAbstentionRequest.model_validate({
            "expires_at": expiry.isoformat(), "decision": "REVIEW_CANDIDATE",
        })
    with pytest.raises(ValidationError):
        ManualAbstentionRequest(expires_at=datetime(2026, 1, 1))
    engine.dispose()


def test_identical_snapshots_are_scoped_to_their_study():
    engine, owner, _, first_study_id = _fixture()
    with Session(engine) as session:
        second = ResearchStudy(
            id=uuid4(), user_id=owner, study_key="nifty50-paper-v2",
            manifest=study(study_key="nifty50-paper-v2").manifest,
            manifest_digest=study(study_key="nifty50-paper-v2").manifest_digest,
            created_at=datetime.now(timezone.utc),
        )
        session.add(second)
        session.commit()
        first = record_recommendation(session, owner, first_study_id, snapshot())
        other = record_recommendation(session, owner, second.id, snapshot())
        assert other.id != first.id
        assert other.study_id == second.id
        assert len(list(session.scalars(select(ResearchRecommendation)))) == 2
    engine.dispose()


def test_recommendation_list_is_newest_first():
    engine, owner, _, study_id = _fixture()
    with Session(engine) as session:
        first = record_recommendation(
            session, owner, study_id, snapshot(selector_version="selector-old"))
        second = record_recommendation(
            session, owner, study_id, snapshot(selector_version="selector-new"))
        first.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        second.created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        session.commit()
        rows = recommendations(study_id, SimpleNamespace(id=owner), session,
                                SimpleNamespace(headers={}), 25, 0)
        assert [row["id"] for row in rows] == [second.id, first.id]
    engine.dispose()


def test_snapshot_rejects_naive_expiry_and_probability_claims():
    with pytest.raises(ValidationError):
        snapshot(expires_at="2026-02-01T10:00:00")
    with pytest.raises(ValidationError):
        snapshot(evidence_snapshot={"profit_probability": 0.8})
    with pytest.raises(ValidationError):
        snapshot(risk_context={"confidence": 0.8})


def test_readiness_policy_builds_deterministic_abstention_without_selection_claim():
    assessment = ReadinessAssessment(
        assessed_at="2026-01-02T10:00:00Z", trial_id=str(uuid4()),
        manifest_digest="a" * 64, status="UNVERIFIED", allowed_uses=[
            "INSPECT_RESEARCH_RECORDS"], checks=[ReadinessCheck(
            key="data", label="Historical data", status="UNKNOWN",
            detail="Availability time is unreviewed.",
        )],
    )
    first = build_abstention_recommendation(
        assessment, selector_version="selector-v1", expires_at="2026-02-01T10:00:00+05:30",
    )
    second = build_abstention_recommendation(
        assessment, selector_version="selector-v1", expires_at="2026-02-01T10:00:00+05:30",
    )
    assert first == second
    assert first.decision == "ABSTAIN"
    assert first.reason_code == "INSUFFICIENT_EVIDENCE"
    assert first.evidence_snapshot["checks"][0]["status"] == "UNKNOWN"
    assert first.risk_context["execution_enabled"] is False


def test_readiness_with_all_pass_checks_still_abstains():
    assessment = ReadinessAssessment(
        assessed_at="2026-01-02T10:00:00Z", trial_id=str(uuid4()),
        manifest_digest="a" * 64, status="UNVERIFIED", allowed_uses=[
            "INSPECT_RESEARCH_RECORDS"], checks=[ReadinessCheck(
            key="manifest_integrity", label="Stored manifest", status="PASS",
            detail="Stored declaration matches its digest.",
        )],
    )
    result = build_abstention_recommendation(
        assessment, selector_version="selector-v1", expires_at="2026-02-01T10:00:00Z",
    )
    assert result.reason_code == "NO_QUALIFIED_STRATEGY"
    assert result.decision == "ABSTAIN"


def test_from_trial_records_deterministic_abstention_without_selection():
    engine, owner, _, study_id = _fixture()
    frozen = study()
    with Session(engine) as session:
        candidate = register_candidate(session, owner, CandidateDefinition(
            name="NIFTY route hypothesis", family="nifty-options", market="NIFTY_OPTIONS",
            hypothesis="A bounded hypothesis used only for readiness review.",
            source_url="https://example.org/nifty-route", source_license="UNREVIEWED",
            entry_rule="Use the frozen incumbent rule.", exit_rule="Use the scheduled replay.",
            limitation="No execution or promotion.",
        ))
        trial = declare_trial(session, owner, candidate.id, TrialManifest(
            dataset_digest=frozen.dataset_digest, calculation_digest=frozen.calculation_digest,
            cost_model_id=frozen.cost_model_id, experiment_family="nifty-options",
            trial_number=1, declared_search_budget=frozen.parent_search_budget, seed=0,
            train_start=frozen.train_start, train_end=frozen.train_end,
            validation_start=frozen.validation_start, validation_end=frozen.validation_end,
            holdout_start=frozen.holdout_start, holdout_end=frozen.holdout_end,
        ))
        from apps.api.research_routes import TrialRecommendationRequest, recommend_from_trial

        result = recommend_from_trial(
            study_id, trial.id,
            TrialRecommendationRequest(
                selector_version="selector-v1", expires_at="2026-02-01T10:00:00Z"),
            SimpleNamespace(id=owner), session,
        )
        assert result["decision"] == "ABSTAIN"
        assert result["snapshot"]["candidate_id"] == str(candidate.id)
        assert result["snapshot"]["trial_id"] == str(trial.id)
        assert result["snapshot"]["risk_context"]["execution_enabled"] is False
        assert result["promotion_eligible"] is False
        assert len(list(session.scalars(select(ResearchRecommendation)))) == 1
    engine.dispose()


def test_from_trial_request_rejects_timezone_naive_expiry():
    from apps.api.research_routes import TrialRecommendationRequest

    with pytest.raises(ValidationError):
        TrialRecommendationRequest(
            selector_version="selector-v1", expires_at="2026-02-01T10:00:00",
        )


def test_market_fit_opportunity_freezes_inputs_and_retains_rejected_alternatives():
    chain = OptionChain(
        underlying="NIFTY", underlying_spot=100, expiry="2026-02-20",
        as_of="2026-02-10T10:00:00+05:30", contracts=[],
    )
    assessment = MarketFitAssessment(
        chain_as_of=chain.as_of,
        chain_digest=fingerprint(chain.model_dump(mode="json")),
        reference_strike=Decimal(100),
        regime_as_of=None, regime="UNKNOWN",
        reasons=["Source and costs are unreviewed."],
        candidates=[MarketFitCandidate(
            strategy_key="bull_call_spread", strategy_version="1.0.0",
            name="Bull call spread", fit="BLOCKED", reasons=["No complete basket."],
            legs=[MarketFitLeg(
                option_type="CE", side="BUY", strike=100, quantity=1,
                entry_quote=5, bid=4, ask=5,
            )],
            maximum_profit=Decimal(10), maximum_loss=Decimal(5),
            entry_policy="Frozen research entry", exit_policy="Frozen research exit",
        )],
    )
    pinned = {("bull_call_spread", "1.0.0")}
    first = build_market_fit_opportunity(
        study(), chain, assessment, policy_set_digest="d" * 64,
        frozen_policy_ids=pinned,
    )
    second = build_market_fit_opportunity(
        study(), chain, assessment, policy_set_digest="d" * 64,
        frozen_policy_ids=pinned,
    )
    assert first == second
    assert first.decision == "ABSTAIN"
    assert first.risk_context["outcome"] == "NOT_ATTEMPTED"
    assert first.evidence_snapshot["study_manifest_digest"] == study().manifest_digest
    assert first.evidence_snapshot["chain_source"] == "CLIENT_SUBMITTED_CHAIN"
    assert first.evidence_snapshot["chain_provider_id"] is None
    assert first.evidence_snapshot["reference_strike"] == "100"
    assert first.evidence_snapshot["chain_expiry"] == "2026-02-20"
    assert first.evidence_snapshot["bar_digest"] is None
    assert first.evidence_snapshot["bar_source"] == "UNAVAILABLE"
    assert first.evidence_snapshot["bar_provider_id"] is None
    assert first.evidence_snapshot["bar_received_at"] is None
    assert first.evidence_snapshot["realized_volatility_annualized_percent"] is None
    assert first.evidence_snapshot["prior_20_bar_high"] is None
    assert first.evidence_snapshot["prior_20_bar_low"] is None
    assert first.evidence_snapshot["technical_level_status"] == "DERIVED_UNVALIDATED"
    assert first.evidence_snapshot["chain_artifact_review"] == "UNREVIEWED_CLIENT_SUBMITTED"
    assert first.evidence_snapshot["bar_artifact_review"] == "UNAVAILABLE"
    assert first.evidence_snapshot["cost_artifact_review"] == "UNREVIEWED_DECLARATION"
    assert first.evidence_snapshot["point_in_time_availability_review"] == "UNVERIFIED"
    assert first.evidence_snapshot["policy_set_digest"] == "d" * 64
    assert first.evidence_snapshot["alternatives"][0]["reasons"] == ["No complete basket."]
    assert first.evidence_snapshot["alternatives"][0]["legs"][0]["entry_quote"] == "5"
    assert (first.evidence_snapshot["alternatives"][0]
            ["maximum_loss_per_option_unit_before_costs"] == "5")
    assert first.evidence_snapshot["alternatives"][0]["frozen_in_study"] is True
    assert first.expires_at == datetime(2026, 2, 10, 4, 35, tzinfo=timezone.utc)

    pinned_bars = assessment.model_copy(update={
        "bar_source": "PROVIDER_HISTORICAL_HASH_ONLY",
        "bar_provider_id": "historical-fixture", "bar_digest": "a" * 64,
        "bar_count": 250, "regime_as_of": chain.as_of - timedelta(minutes=5),
        "bar_received_at": chain.as_of + timedelta(seconds=1),
        "realized_volatility_annualized_percent": Decimal("14.25"),
        "prior_20_bar_high": Decimal(108), "prior_20_bar_low": Decimal(92),
    })
    with_bars = build_market_fit_opportunity(study(), chain, pinned_bars)
    assert with_bars.evidence_snapshot["bar_digest"] == "a" * 64
    assert with_bars.evidence_snapshot["bar_provider_id"] == "historical-fixture"
    assert with_bars.evidence_snapshot["bar_count"] == 250
    assert with_bars.evidence_snapshot["bar_received_at"] == (
        chain.as_of + timedelta(seconds=1)).isoformat()
    assert with_bars.evidence_snapshot["bar_artifact_review"] == "UNREVIEWED_HASH_ONLY"
    assert with_bars.evidence_snapshot["realized_volatility_annualized_percent"] == "14.25"
    assert with_bars.evidence_snapshot["prior_20_bar_high"] == "108"
    assert with_bars.evidence_snapshot["prior_20_bar_low"] == "92"
    assert with_bars.risk_context["costs_qualified"] is False

    for invalid in (
        assessment.model_copy(update={"bar_digest": "a" * 64}),
        pinned_bars.model_copy(update={"bar_count": 249}),
        pinned_bars.model_copy(update={"bar_provider_id": None}),
        pinned_bars.model_copy(update={"bar_received_at": None}),
        pinned_bars.model_copy(update={"bar_received_at": chain.as_of - timedelta(seconds=1)}),
        pinned_bars.model_copy(update={"bar_received_at": chain.as_of + timedelta(minutes=6)}),
        pinned_bars.model_copy(update={"regime_as_of": chain.as_of + timedelta(minutes=1)}),
    ):
        with pytest.raises(ValueError, match="bars"):
            build_market_fit_opportunity(study(), chain, invalid)

    changed_chain = chain.model_copy(update={"underlying_spot": Decimal(101)})
    with pytest.raises(ValueError, match="differs from the assessed chain"):
        build_market_fit_opportunity(study(), changed_chain, assessment)

    with pytest.raises(ValueError, match="provider and receipt identity"):
        build_market_fit_opportunity(study(), chain, assessment.model_copy(update={
            "chain_source": "SERVER_FETCHED_OPTION_CHAIN",
        }))

    with pytest.raises(ValueError, match="server receipt identity"):
        build_market_fit_opportunity(study(), chain, assessment.model_copy(update={
            "chain_received_at": chain.as_of,
        }))

    with pytest.raises(ValueError, match="receipt must follow"):
        build_market_fit_opportunity(study(), chain, assessment.model_copy(update={
            "chain_source": "SERVER_FETCHED_OPTION_CHAIN",
            "chain_provider_id": "test-provider",
            "chain_received_at": chain.as_of - timedelta(seconds=1),
        }))

    with pytest.raises(ValueError, match="providers must match"):
        build_market_fit_opportunity(study(), chain, pinned_bars.model_copy(update={
            "chain_source": "SERVER_FETCHED_OPTION_CHAIN",
            "chain_provider_id": "other-provider",
            "chain_received_at": chain.as_of + timedelta(seconds=1),
        }))

    with pytest.raises(ValueError, match="after the chain receipt cutoff"):
        build_market_fit_opportunity(study(), chain, pinned_bars.model_copy(update={
            "chain_source": "SERVER_FETCHED_OPTION_CHAIN",
            "chain_provider_id": "historical-fixture",
            "chain_received_at": chain.as_of,
        }))

    with pytest.raises(ValueError, match="protected holdout"):
        build_market_fit_opportunity(study(holdout_end="2026-02-11"), chain, assessment)

    expired_chain = chain.model_copy(update={"expiry": date(2026, 2, 9)})
    expired_assessment = assessment.model_copy(update={
        "chain_digest": fingerprint(expired_chain.model_dump(mode="json")),
    })
    with pytest.raises(ValueError, match="expired chain"):
        build_market_fit_opportunity(study(), expired_chain, expired_assessment)


def test_market_fit_opportunity_route_is_owned_current_and_idempotent(monkeypatch):
    from apps.api import main

    engine, owner, other, study_id = _fixture()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    chain = OptionChain(
        underlying="NIFTY", underlying_spot=100,
        expiry=(now + timedelta(days=7)).date(), as_of=now, contracts=[],
    )
    body = MarketFitRequest(chain=chain, reference_strike=100)
    assessment = MarketFitAssessment(
        chain_as_of=chain.as_of,
        chain_digest=fingerprint(chain.model_dump(mode="json")),
        reference_strike=Decimal(100),
        regime_as_of=None, regime="UNKNOWN",
        reasons=["No qualified policy evidence."], candidates=[],
    )

    async def assess(_user, request):
        return assessment.model_copy(update={
            "chain_as_of": request.chain.as_of,
            "chain_digest": fingerprint(request.chain.model_dump(mode="json")),
        })

    monkeypatch.setattr(main, "analytics_options_market_fit", assess)
    with Session(engine) as session:
        with pytest.raises(HTTPException) as missing:
            asyncio.run(main.record_nifty_market_fit_opportunity(
                study_id, body, SimpleNamespace(id=other), session))
        assert missing.value.status_code == 404
        with pytest.raises(HTTPException) as unfrozen:
            asyncio.run(main.record_nifty_market_fit_opportunity(
                study_id, body, SimpleNamespace(id=owner), session))
        assert unfrozen.value.status_code == 409
        freeze_policy_set(session, owner, study_id, FrozenPolicySet(
            incumbent={
                "strategy_key": "bull_call_spread", "strategy_version": "1.0.0",
                "policy_version": "nifty-paper-v1", "policy_artifact_digest": "c" * 64,
                "entry_rule": "Use only the frozen completed-bar entry window.",
                "exit_rule": "Use only the frozen stop, target and time exit.",
                "risk_rule": "Abstain when basket, capital or costs are unavailable.",
            },
            limitation="Research-only policy identity; source evidence remains unreviewed.",
        ))
        first = asyncio.run(main.record_nifty_market_fit_opportunity(
            study_id, body, SimpleNamespace(id=owner), session))
        replay = asyncio.run(main.record_nifty_market_fit_opportunity(
            study_id, body, SimpleNamespace(id=owner), session))
        assert first["id"] == replay["id"]
        assert first["decision"] == "ABSTAIN"
        assert len(list(session.scalars(select(ResearchRecommendation)))) == 1
        late_bars = assessment.model_copy(update={
            "bar_source": "PROVIDER_HISTORICAL_HASH_ONLY",
            "bar_provider_id": "test-provider", "bar_digest": "a" * 64,
            "bar_count": 250, "bar_received_at": now + timedelta(seconds=2),
            "regime_as_of": now - timedelta(minutes=5),
            "regime": "TRENDING_UP",
            "realized_volatility_annualized_percent": Decimal("14.25"),
        })

        async def assess_late(_user, _request):
            return late_bars

        monkeypatch.setattr(main, "analytics_options_market_fit", assess_late)
        later = asyncio.run(main._record_nifty_market_fit_opportunity(
            study_id, body, SimpleNamespace(id=owner), session,
            chain_provider_id="test-provider", chain_received_at=now + timedelta(seconds=1),
        ))
        snapshot = later["snapshot"]["evidence_snapshot"]
        assert snapshot["chain_source"] == "SERVER_FETCHED_OPTION_CHAIN"
        assert snapshot["bar_source"] == "UNAVAILABLE"
        assert snapshot["bar_digest"] is None
        assert snapshot["realized_volatility_annualized_percent"] is None
        assert snapshot["regime"] == "UNKNOWN"
        assert "Historical bars arrived after the option-chain receipt cutoff." in later[
            "snapshot"]["rejected_reasons"]
        assert later["decision"] == "ABSTAIN"
        assert len(list(session.scalars(select(ResearchRecommendation)))) == 2
        stale = MarketFitRequest(
            chain=chain.model_copy(update={"as_of": now - timedelta(minutes=6)}),
            reference_strike=100,
        )
        with pytest.raises(HTTPException) as late:
            asyncio.run(main.record_nifty_market_fit_opportunity(
                study_id, stale, SimpleNamespace(id=owner), session))
        assert late.value.status_code == 422
        expired = MarketFitRequest(
            chain=chain.model_copy(update={"expiry": (now - timedelta(days=1)).date()}),
            reference_strike=100,
        )
        with pytest.raises(HTTPException) as past_expiry:
            asyncio.run(main.record_nifty_market_fit_opportunity(
                study_id, expired, SimpleNamespace(id=owner), session))
        assert past_expiry.value.status_code == 409
        assert len(list(session.scalars(select(ResearchRecommendation)))) == 2
    engine.dispose()
