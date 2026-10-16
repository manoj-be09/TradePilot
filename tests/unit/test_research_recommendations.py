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
from packages.regime.frozen_nifty_study import FrozenNiftyStudy
from packages.regime.research_recommendations import (
    RecommendationSnapshot,
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


def test_snapshot_rejects_naive_expiry_and_probability_claims():
    with pytest.raises(ValidationError):
        snapshot(expires_at="2026-02-01T10:00:00")
    with pytest.raises(ValidationError):
        snapshot(evidence_snapshot={"profit_probability": 0.8})
    with pytest.raises(ValidationError):
        snapshot(risk_context={"confidence": 0.8})
