from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.database.base import Base
from packages.database.models import ResearchRecommendation, ResearchStudy, User
from packages.regime.candidate_registry import fingerprint
from packages.regime.frozen_nifty_study import FrozenNiftyStudy
from packages.regime.research_policy_sets import (
    FrozenPolicySet,
    current_policy_set_ids,
    freeze_policy_set,
    policy_set_view,
)


def _study():
    return FrozenNiftyStudy(
        study_key="nifty-policy-v1",
        objective="Compare one incumbent, two challengers and no trade prospectively.",
        incumbent_rule_version="incumbent-v1", dataset_digest="a" * 64,
        calculation_digest="b" * 64, cost_model_id="illustrative-v1",
        capital="100000.00", parent_search_budget=3, review_interval_days=30,
        uncertainty_note="Costs and fills remain unreviewed; no advantage is claimed.",
        train_start="2020-01-01", train_end="2022-12-31",
        validation_start="2023-01-01", validation_end="2024-12-31",
        holdout_start="2025-01-01", holdout_end="2025-12-31",
    )


def _policy(key, **changes):
    return {
        "strategy_key": key, "strategy_version": "1.0.0",
        "policy_version": "incumbent-v1",
        "policy_artifact_digest": "c" * 64,
        "entry_rule": "Enter only after the frozen completed bar and basket checks.",
        "exit_rule": "Exit only under the frozen time, stop and target policy.",
        "risk_rule": "Abstain on stale data, incomplete legs or inadequate capital.",
    } | changes


def _policies(**changes):
    return FrozenPolicySet(**({
        "incumbent": _policy("bull_call_spread"),
        "challengers": [_policy("bear_put_spread"), _policy("iron_condor")],
        "limitation": "Research-only contract; costs, fills and profitability unverified.",
    } | changes))


def test_policy_set_is_frozen_account_scoped_and_idempotent():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner, other = uuid4(), uuid4()
    frozen = _study()
    with Session(engine) as session:
        session.add_all([
            User(id=owner, email="policy-owner@example.org", password_hash="fixture"),
            User(id=other, email="policy-other@example.org", password_hash="fixture"),
        ])
        study_row = ResearchStudy(
            id=uuid4(), user_id=owner, study_key=frozen.study_key,
            manifest=frozen.manifest, manifest_digest=frozen.manifest_digest,
            created_at=datetime.now(timezone.utc),
        )
        session.add(study_row)
        session.commit()
        with pytest.raises(LookupError):
            freeze_policy_set(session, other, study_row.id, _policies())
        row = freeze_policy_set(session, owner, study_row.id, _policies())
        assert freeze_policy_set(session, owner, study_row.id, _policies()).id == row.id
        assert row.manifest["study_manifest_digest"] == frozen.manifest_digest
        assert row.manifest["parent_search_budget"] == 3
        assert len(row.manifest["challengers"]) == 2
        assert len(row.manifest["incumbent"]["template_digest"]) == 64
        assert current_policy_set_ids(row) == {
            ("bull_call_spread", "1.0.0"),
            ("bear_put_spread", "1.0.0"),
            ("iron_condor", "1.0.0"),
        }
        assert policy_set_view(row)["execution_enabled"] is False
        with pytest.raises(ValueError, match="different immutable"):
            freeze_policy_set(session, owner, study_row.id, _policies(
                challengers=[_policy("bear_put_spread")]))
        with pytest.raises(ValueError, match="template version"):
            freeze_policy_set(session, owner, study_row.id, _policies(
                incumbent=_policy("bull_call_spread", strategy_version="9.0.0")))
        with pytest.raises(ValueError, match="Incumbent policy version"):
            freeze_policy_set(session, owner, study_row.id, _policies(
                incumbent=_policy("bull_call_spread", policy_version="different-v2")))
        session.add(ResearchRecommendation(
            user_id=owner, study_id=study_row.id, decision="ABSTAIN",
            reason_code="INSUFFICIENT_EVIDENCE", snapshot={},
            snapshot_digest="d" * 64, expires_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        ))
        session.commit()
        assert freeze_policy_set(session, owner, study_row.id, _policies()).id == row.id
        original = row.manifest
        row.manifest = {
            **original, "incumbent": {**original["incumbent"], "template_digest": "e" * 64},
        }
        row.manifest_digest = fingerprint(row.manifest)
        with pytest.raises(ValueError, match="Frozen template differs"):
            current_policy_set_ids(row)
    engine.dispose()


def test_policy_set_rejects_duplicate_or_excess_challengers_and_late_freeze():
    with pytest.raises(ValidationError):
        _policies(challengers=[_policy("bull_call_spread")])
    with pytest.raises(ValidationError):
        _policies(challengers=[_policy("bear_put_spread"), _policy("iron_condor"),
                               _policy("bull_call_spread")])
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner = uuid4()
    frozen = _study()
    with Session(engine) as session:
        session.add(User(id=owner, email="late-policy@example.org", password_hash="fixture"))
        study_row = ResearchStudy(
            id=uuid4(), user_id=owner, study_key=frozen.study_key,
            manifest=frozen.manifest, manifest_digest=frozen.manifest_digest,
            created_at=datetime.now(timezone.utc),
        )
        session.add(study_row)
        session.commit()
        session.add(ResearchRecommendation(
            user_id=owner, study_id=study_row.id, decision="ABSTAIN",
            reason_code="INSUFFICIENT_EVIDENCE", snapshot={},
            snapshot_digest="d" * 64, expires_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        ))
        session.commit()
        with pytest.raises(ValueError, match="before recommendation"):
            freeze_policy_set(session, owner, study_row.id, _policies())
    engine.dispose()
