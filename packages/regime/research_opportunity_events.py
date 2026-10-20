"""Append-only, unqualified shadow-outcome observations; never paper fills."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.database.models import ResearchOpportunityEvent, ResearchRecommendation
from packages.regime.candidate_registry import fingerprint


class OpportunityEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_key: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    status: Literal["UNRESOLVED", "SHADOW_NO_FILL", "SHADOW_REJECTED"]
    reason_code: Literal["OUTCOME_PENDING", "MARKET_DATA_GAP", "BASKET_NOT_FILLABLE",
                         "RISK_GATE", "POLICY_OUT_OF_SCOPE"]
    strategy_key: str | None = Field(default=None, min_length=3, max_length=64)
    strategy_version: str | None = Field(default=None, min_length=1, max_length=64)
    observed_at: datetime
    available_at: datetime
    source_artifact_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_kind: Literal["OPERATOR_RECORDED_SHADOW"] = "OPERATOR_RECORDED_SHADOW"

    @model_validator(mode="after")
    def consistent(self) -> "OpportunityEventInput":
        allowed = {
            "UNRESOLVED": {"OUTCOME_PENDING", "MARKET_DATA_GAP"},
            "SHADOW_NO_FILL": {"BASKET_NOT_FILLABLE"},
            "SHADOW_REJECTED": {"RISK_GATE", "POLICY_OUT_OF_SCOPE"},
        }
        if self.reason_code not in allowed[self.status]:
            raise ValueError("Status and reason code disagree")
        if (self.strategy_key is None) != (self.strategy_version is None):
            raise ValueError("Candidate key and version must be supplied together")
        if self.status != "UNRESOLVED" and self.strategy_key is None:
            raise ValueError("Shadow no-fill/rejection requires exact candidate identity")
        if self.observed_at.tzinfo is None or self.available_at.tzinfo is None:
            raise ValueError("Observation and availability times must include timezones")
        if self.available_at < self.observed_at:
            raise ValueError("Evidence cannot be available before observation")
        return self


def record_opportunity_event(session: Session, owner: UUID, recommendation_id: UUID,
                             event: OpportunityEventInput) -> ResearchOpportunityEvent:
    recommendation = session.scalar(select(ResearchRecommendation).where(
        ResearchRecommendation.id == recommendation_id,
        ResearchRecommendation.user_id == owner,
    ))
    if recommendation is None:
        raise LookupError("Opportunity not found")
    snapshot = recommendation.snapshot
    evidence = snapshot.get("evidence_snapshot", {})
    if (evidence.get("kind") != "NIFTY_MARKET_FIT_OPPORTUNITY_V1"
            or recommendation.decision != "ABSTAIN"
            or fingerprint({"study_id": str(recommendation.study_id),
                            "snapshot": snapshot}) != recommendation.snapshot_digest):
        raise ValueError("Recommendation is not an intact market-fit opportunity")
    as_of = datetime.fromisoformat(evidence["chain_as_of"])
    now = datetime.now(timezone.utc)
    if (event.observed_at < as_of or event.available_at > now
            or event.observed_at > now):
        raise ValueError("Outcome evidence is future-dated or precedes the opportunity")
    if event.strategy_key is not None:
        alternatives = evidence.get("alternatives", [])
        matching = [item for item in alternatives
                    if item.get("strategy_key") == event.strategy_key
                    and item.get("strategy_version") == event.strategy_version]
        if not matching:
            raise ValueError("Candidate is not present in the frozen opportunity")
        if event.status != "UNRESOLVED" and (
            evidence.get("policy_set_digest") is None
            or not matching[0].get("frozen_in_study")
        ):
            raise ValueError("Shadow outcome requires a frozen study policy")
    payload = event.model_dump(mode="json")
    digest = fingerprint(payload)
    existing = session.scalar(select(ResearchOpportunityEvent).where(
        ResearchOpportunityEvent.recommendation_id == recommendation_id,
        ResearchOpportunityEvent.event_key == event.event_key,
    ))
    if existing is not None:
        if existing.user_id == owner and existing.payload_digest == digest:
            return existing
        raise ValueError("Event key already has different immutable evidence")
    row = ResearchOpportunityEvent(
        user_id=owner, recommendation_id=recommendation_id,
        event_key=event.event_key, status=event.status,
        payload=payload, payload_digest=digest, created_at=now,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(ResearchOpportunityEvent).where(
            ResearchOpportunityEvent.recommendation_id == recommendation_id,
            ResearchOpportunityEvent.event_key == event.event_key,
        ))
        if existing is None or existing.user_id != owner or existing.payload_digest != digest:
            raise
        return existing
    return row


def opportunity_event_view(row: ResearchOpportunityEvent) -> dict[str, object]:
    return {
        "id": row.id, "recommendation_id": row.recommendation_id,
        "event_key": row.event_key, "status": row.status, "payload": row.payload,
        "payload_digest": row.payload_digest, "created_at": row.created_at,
        "evidence_qualification": "UNVERIFIED_SHADOW",
        "execution_enabled": False, "promotion_eligible": False,
    }
