"""Prospective research-only recommendation and abstention records."""
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.database.models import ResearchRecommendation, ResearchStudy
from packages.market_data.contracts import OptionChain
from packages.options.market_fit import MarketFitAssessment
from packages.regime.candidate_registry import fingerprint
from packages.regime.frozen_nifty_study import FrozenNiftyStudy
from packages.regime.research_readiness import ReadinessAssessment


class RecommendationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["ABSTAIN", "REVIEW_CANDIDATE"]
    reason_code: Literal["NO_QUALIFIED_STRATEGY", "INSUFFICIENT_EVIDENCE",
                         "DATA_UNAVAILABLE", "CAPACITY_CONSTRAINT", "REVIEW_ONLY"]
    selector_version: str = Field(min_length=1, max_length=64)
    candidate_id: UUID | None = None
    trial_id: UUID | None = None
    evidence_snapshot: dict[str, Any] = Field(default_factory=dict)
    alternatives: list[str] = Field(default_factory=list, max_length=20)
    rejected_reasons: list[str] = Field(default_factory=list, max_length=20)
    risk_context: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def utc_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Recommendation expiry must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("evidence_snapshot", "risk_context")
    @classmethod
    def forbid_forecast_claims(cls, value: dict[str, Any]) -> dict[str, Any]:
        forbidden = {"profit_probability", "win_probability", "confidence"}
        if forbidden.intersection(value):
            raise ValueError("Recommendation snapshots cannot claim calibrated probability")
        return value


def build_abstention_recommendation(
    assessment: ReadinessAssessment, *, selector_version: str,
    expires_at: datetime,
) -> RecommendationSnapshot:
    """Build a deterministic research-only abstention from stored readiness.

    Readiness is a gate on evidence usability, not a strategy score. This helper
    deliberately never emits a candidate selection or a probability; a caller
    must persist the returned snapshot explicitly and review it separately.
    """
    blockers = [f"{check.key}:{check.status}" for check in assessment.checks
                if check.status != "PASS"]
    reason = "INSUFFICIENT_EVIDENCE" if blockers else "NO_QUALIFIED_STRATEGY"
    checks = [{
        "key": check.key, "status": check.status,
        "artifact_ref": check.evidence_ref,
        "artifact_digest": check.artifact_digest,
        "artifact_version": check.artifact_version,
        "allowed_use": check.allowed_use,
    } for check in assessment.checks]
    return RecommendationSnapshot(
        decision="ABSTAIN", reason_code=reason, selector_version=selector_version,
        evidence_snapshot={
            "assessment_policy_version": assessment.policy_version,
            "assessment_status": assessment.status,
            "manifest_digest": assessment.manifest_digest,
            "checks": checks,
        },
        rejected_reasons=blockers or ["No reviewed strategy is qualified for selection."],
        risk_context={
            "allowed_uses": assessment.allowed_uses,
            "execution_enabled": False,
            "promotion_eligible": False,
        },
        expires_at=expires_at,
    )


def build_market_fit_opportunity(
    study: FrozenNiftyStudy, chain: OptionChain, assessment: MarketFitAssessment,
) -> RecommendationSnapshot:
    """Freeze one unqualified, point-in-time abstention for later review.

    The chain is caller-submitted, so its hash identifies bytes supplied to this
    calculation; it does not certify exchange timing, source rights, or fills.
    """
    if chain.underlying != "NIFTY" or assessment.chain_as_of != chain.as_of:
        raise ValueError("Opportunity does not match its NIFTY chain")
    if assessment.action != "NO_TRADE" or assessment.probability_of_profit is not None:
        raise ValueError("Only unqualified no-trade opportunities can be recorded")
    if chain.as_of.tzinfo is None:
        raise ValueError("Opportunity time must include a timezone")
    as_of = chain.as_of.astimezone(timezone.utc)
    if as_of.astimezone(ZoneInfo("Asia/Kolkata")).date() <= study.holdout_end:
        raise ValueError("Prospective opportunity must follow the protected holdout")
    alternatives = [{
        "strategy_key": item.strategy_key,
        "strategy_version": item.strategy_version,
        "fit": item.fit,
        "reasons": item.reasons,
        "entry_policy": item.entry_policy,
        "exit_policy": item.exit_policy,
        "fill_status": "NOT_ATTEMPTED",
    } for item in assessment.candidates]
    return RecommendationSnapshot(
        decision="ABSTAIN", reason_code="INSUFFICIENT_EVIDENCE",
        selector_version=assessment.selector_version,
        evidence_snapshot={
            "kind": "NIFTY_MARKET_FIT_OPPORTUNITY_V1",
            "study_manifest_digest": study.manifest_digest,
            "chain_digest": fingerprint(chain.model_dump(mode="json")),
            "chain_source": assessment.chain_source,
            "chain_as_of": as_of.isoformat(),
            "bar_source": "PROVIDER_HISTORICAL_UNPINNED"
            if assessment.regime_as_of else "UNAVAILABLE",
            "bar_digest": None,
            "regime": assessment.regime,
            "regime_as_of": assessment.regime_as_of.isoformat()
            if assessment.regime_as_of else None,
            "cost_model_id": study.cost_model_id,
            "cost_artifact_digest": study.cost_artifact_digest,
            "study_objective": study.objective,
            "incumbent_rule_version": study.incumbent_rule_version,
            "capital": str(study.capital),
            "review_interval_days": study.review_interval_days,
            "uncertainty_note": study.uncertainty_note,
            "dataset_digest": study.dataset_digest,
            "holdout_artifact_digest": study.holdout_artifact_digest,
            "parent_search_budget": study.parent_search_budget,
            "alternatives": alternatives,
            "action": "NO_TRADE",
        },
        alternatives=[f"{item.strategy_key}@{item.strategy_version}"
                      for item in assessment.candidates],
        rejected_reasons=assessment.reasons,
        risk_context={
            "execution_enabled": False, "promotion_eligible": False,
            "costs_qualified": False, "book_timing_qualified": False,
            "outcome": "NOT_ATTEMPTED",
        },
        expires_at=as_of + timedelta(minutes=5),
    )


def record_recommendation(session: Session, owner: UUID, study_id: UUID,
                          snapshot: RecommendationSnapshot) -> ResearchRecommendation:
    if session.scalar(select(ResearchStudy.id).where(
        ResearchStudy.id == study_id, ResearchStudy.user_id == owner,
    )) is None:
        raise LookupError("Study not found")
    payload = snapshot.model_dump(mode="json")
    digest = fingerprint({"study_id": str(study_id), "snapshot": payload})
    legacy_digest = fingerprint(payload)
    existing = session.scalar(select(ResearchRecommendation).where(
        ResearchRecommendation.user_id == owner,
        ResearchRecommendation.study_id == study_id,
        ResearchRecommendation.snapshot_digest.in_((digest, legacy_digest)),
    ))
    if existing is not None:
        return existing
    row = ResearchRecommendation(
        user_id=owner, study_id=study_id, decision=snapshot.decision,
        reason_code=snapshot.reason_code, snapshot=payload, snapshot_digest=digest,
        expires_at=snapshot.expires_at, created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(ResearchRecommendation).where(
            ResearchRecommendation.user_id == owner,
            ResearchRecommendation.study_id == study_id,
            ResearchRecommendation.snapshot_digest.in_((digest, legacy_digest)),
        ))
        if existing is None:
            raise
        return existing
    return row


def recommendation_view(row: ResearchRecommendation) -> dict[str, Any]:
    return {"id": row.id, "study_id": row.study_id, "decision": row.decision,
            "reason_code": row.reason_code, "snapshot": row.snapshot,
            "snapshot_digest": row.snapshot_digest, "expires_at": row.expires_at,
            "created_at": row.created_at, "execution_enabled": False,
            "promotion_eligible": False}
