from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.research_routes import (
    create_recommendation,
    recommendations,
)
from packages.database.base import Base
from packages.database.models import ResearchRecommendation, ResearchStudy, User
from packages.regime.candidate_registry import (
    CandidateDefinition,
    TrialManifest,
    declare_trial,
    register_candidate,
)
from packages.regime.frozen_nifty_study import FrozenNiftyStudy
from packages.regime.research_readiness import ReadinessAssessment, ReadinessCheck
from packages.regime.research_recommendations import (
    RecommendationSnapshot,
    build_abstention_recommendation,
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
        view = create_recommendation(study_id, snapshot(), SimpleNamespace(id=owner), session)
        assert view["id"] == first.id
        assert view["execution_enabled"] is False
        assert view["promotion_eligible"] is False
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
