from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from packages.database.base import Base
from packages.database.models import (
    ResearchOpportunityEvent,
    ResearchPolicySet,
    ResearchStudy,
    User,
)
from packages.regime.candidate_registry import fingerprint
from packages.regime.research_opportunity_events import (
    OpportunityEventInput,
    opportunity_event_view,
    record_opportunity_event,
)
from packages.regime.research_recommendations import (
    RecommendationSnapshot,
    record_recommendation,
)


def _event(now, **changes):
    return OpportunityEventInput(**({
        "event_key": "shadow-basket-1", "status": "SHADOW_NO_FILL",
        "reason_code": "BASKET_NOT_FILLABLE",
        "strategy_key": "bull_call_spread", "strategy_version": "1.0.0",
        "observed_at": now - timedelta(minutes=1),
        "available_at": now - timedelta(seconds=30),
        "source_artifact_digest": "e" * 64,
    } | changes))


def test_shadow_no_fill_is_append_only_owned_and_unqualified():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner, other = uuid4(), uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session(engine) as session:
        session.add_all([
            User(id=owner, email="event-owner@example.org", password_hash="fixture"),
            User(id=other, email="event-other@example.org", password_hash="fixture"),
        ])
        study = ResearchStudy(
            id=uuid4(), user_id=owner, study_key="event-study",
            manifest={}, manifest_digest="a" * 64, created_at=now,
        )
        session.add(study)
        policy_manifest = {
            "study_manifest_digest": study.manifest_digest,
            "incumbent": {
                "strategy_key": "bull_call_spread",
                "strategy_version": "1.0.0",
                "policy_version": "nifty-paper-v1",
                "policy_artifact_digest": "c" * 64,
                "template_digest": "d" * 64,
            },
            "challengers": [],
        }
        policy_set = ResearchPolicySet(
            id=uuid4(), user_id=owner, study_id=study.id,
            manifest=policy_manifest, manifest_digest=fingerprint(policy_manifest),
            created_at=now,
        )
        session.add(policy_set)
        session.commit()
        snapshot = RecommendationSnapshot(
            decision="ABSTAIN", reason_code="INSUFFICIENT_EVIDENCE",
            selector_version="fit-v0", expires_at=now + timedelta(minutes=5),
            evidence_snapshot={
                "kind": "NIFTY_MARKET_FIT_OPPORTUNITY_V1",
                "chain_as_of": (now - timedelta(minutes=2)).isoformat(),
                "study_manifest_digest": study.manifest_digest,
                "policy_set_digest": policy_set.manifest_digest,
                "alternatives": [{
                    "strategy_key": "bull_call_spread", "strategy_version": "1.0.0",
                    "frozen_in_study": True,
                }],
            },
        )
        recommendation = record_recommendation(session, owner, study.id, snapshot)
        with pytest.raises(LookupError):
            record_opportunity_event(session, other, recommendation.id, _event(now))
        first = record_opportunity_event(session, owner, recommendation.id, _event(now))
        replay = record_opportunity_event(session, owner, recommendation.id, _event(now))
        assert replay.id == first.id
        assert opportunity_event_view(first)["evidence_qualification"] == "UNVERIFIED_SHADOW"
        assert opportunity_event_view(first)["promotion_eligible"] is False
        assert first.payload["frozen_policy_binding"] == {
            "policy_set_digest": policy_set.manifest_digest,
            "policy_version": "nifty-paper-v1",
            "policy_artifact_digest": "c" * 64,
            "template_digest": "d" * 64,
        }
        assert len(list(session.scalars(select(ResearchOpportunityEvent)))) == 1
        legacy_input = _event(now, event_key="legacy-shadow")
        legacy_payload = legacy_input.model_dump(mode="json")
        legacy = ResearchOpportunityEvent(
            id=uuid4(), user_id=owner, recommendation_id=recommendation.id,
            event_key=legacy_input.event_key, status=legacy_input.status,
            payload=legacy_payload, payload_digest=fingerprint(legacy_payload),
            created_at=now,
        )
        session.add(legacy)
        session.commit()
        assert record_opportunity_event(
            session, owner, recommendation.id, legacy_input,
        ).id == legacy.id
        with pytest.raises(ValueError, match="different immutable"):
            record_opportunity_event(session, owner, recommendation.id, _event(
                now, source_artifact_digest="f" * 64))
        with pytest.raises(ValueError, match="future-dated"):
            record_opportunity_event(session, owner, recommendation.id, _event(
                now, event_key="future", available_at=now + timedelta(minutes=1)))
        with pytest.raises(ValueError, match="precedes"):
            record_opportunity_event(session, owner, recommendation.id, _event(
                now, event_key="early", observed_at=now - timedelta(minutes=3),
                available_at=now - timedelta(minutes=3)))
        unresolved = _event(
            now, event_key="unresolved", status="UNRESOLVED",
            reason_code="OUTCOME_PENDING", strategy_key=None, strategy_version=None,
        )
        assert record_opportunity_event(session, owner, recommendation.id,
                                        unresolved).status == "UNRESOLVED"
        rejected = _event(now, event_key="risk-rejection",
                          status="SHADOW_REJECTED", reason_code="RISK_GATE")
        rejected_row = record_opportunity_event(
            session, owner, recommendation.id, rejected,
        )
        assert rejected_row.payload["frozen_policy_binding"] == (
            first.payload["frozen_policy_binding"])
        unpinned = snapshot.model_copy(deep=True)
        unpinned.evidence_snapshot["policy_set_digest"] = None
        unpinned.evidence_snapshot["alternatives"][0]["frozen_in_study"] = False
        other_opportunity = record_recommendation(session, owner, study.id, unpinned)
        with pytest.raises(ValueError, match="frozen study policy"):
            record_opportunity_event(session, owner, other_opportunity.id, _event(now))
        mismatched = snapshot.model_copy(deep=True)
        mismatched.evidence_snapshot["policy_set_digest"] = "f" * 64
        bad_opportunity = record_recommendation(session, owner, study.id, mismatched)
        with pytest.raises(ValueError, match="Frozen policy artifact"):
            record_opportunity_event(session, owner, bad_opportunity.id, _event(now))
        session.delete(policy_set)
        session.commit()
        with pytest.raises(ValueError, match="Frozen policy artifact"):
            record_opportunity_event(session, owner, recommendation.id,
                                     _event(now, event_key="missing-policy"))
    engine.dispose()


def test_event_input_and_unfrozen_shadow_fail_closed():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        _event(now, status="SHADOW_REJECTED")
    with pytest.raises(ValidationError):
        _event(now, observed_at=now, available_at=now - timedelta(minutes=1))
    with pytest.raises(ValidationError):
        _event(now, status="SHADOW_NO_FILL", strategy_key=None,
               strategy_version=None)
