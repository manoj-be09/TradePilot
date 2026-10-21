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
    *, policy_set_digest: str | None = None,
    frozen_policy_ids: set[tuple[str, str]] | None = None,
) -> RecommendationSnapshot:
    """Freeze one unqualified, point-in-time abstention for later review.

    The chain hash identifies the assessed bytes; neither caller submission nor
    server fetch alone certifies exchange timing, source rights, or fills.
    """
    if chain.underlying != "NIFTY" or assessment.chain_as_of != chain.as_of:
        raise ValueError("Opportunity does not match its NIFTY chain")
    if assessment.chain_digest != fingerprint(chain.model_dump(mode="json")):
        raise ValueError("Opportunity chain differs from the assessed chain")
    if assessment.chain_source == "SERVER_FETCHED_OPTION_CHAIN":
        received = assessment.chain_received_at
        if not assessment.chain_provider_id or received is None or received.tzinfo is None:
            raise ValueError("Server-fetched chain requires provider and receipt identity")
        if not timedelta(0) <= received - chain.as_of <= timedelta(minutes=5):
            raise ValueError("Server-fetched chain receipt must follow its current snapshot")
        if (assessment.bar_source != "UNAVAILABLE"
                and assessment.bar_provider_id != assessment.chain_provider_id):
            raise ValueError("Chain and historical-bar providers must match")
    elif assessment.chain_received_at is not None:
        raise ValueError("Client chain cannot claim server receipt identity")
    if assessment.reference_strike is None:
        raise ValueError("Opportunity lacks its assessed reference strike")
    if assessment.action != "NO_TRADE" or assessment.probability_of_profit is not None:
        raise ValueError("Only unqualified no-trade opportunities can be recorded")
    if chain.as_of.tzinfo is None:
        raise ValueError("Opportunity time must include a timezone")
    if chain.expiry < chain.as_of.astimezone(ZoneInfo("Asia/Kolkata")).date():
        raise ValueError("Prospective opportunity cannot use an expired chain")
    if assessment.bar_source == "UNAVAILABLE":
        if any(value is not None for value in (
            assessment.bar_provider_id, assessment.bar_digest, assessment.bar_count,
            assessment.bar_received_at,
        )):
            raise ValueError("Unavailable bars cannot carry a provider artifact identity")
        if any(value is not None for value in (
            assessment.realized_volatility_annualized_percent,
            assessment.prior_20_bar_high,
            assessment.prior_20_bar_low,
        )):
            raise ValueError("Unavailable bars cannot carry derived technical context")
    elif (
        assessment.bar_digest is None
        or not assessment.bar_provider_id
        or assessment.bar_count != 250
        or assessment.bar_received_at is None
        or assessment.bar_received_at.tzinfo is None
        or assessment.regime_as_of is None
        or assessment.regime_as_of.tzinfo is None
        or not timedelta(0) <= chain.as_of - assessment.regime_as_of <= timedelta(minutes=15)
        or not timedelta(0) <= assessment.bar_received_at - chain.as_of <= timedelta(minutes=5)
    ):
        raise ValueError("Hashed provider bars require the frozen completed-bar window")
    if (assessment.chain_source == "SERVER_FETCHED_OPTION_CHAIN"
            and assessment.bar_received_at is not None
            and assessment.chain_received_at is not None
            and assessment.bar_received_at > assessment.chain_received_at):
        raise ValueError("Provider bars arrived after the chain receipt cutoff")
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
        "legs": [leg.model_dump(mode="json") for leg in item.legs],
        "maximum_profit_per_option_unit_before_costs":
        str(item.maximum_profit) if item.maximum_profit is not None else None,
        "maximum_loss_per_option_unit_before_costs":
        str(item.maximum_loss) if item.maximum_loss is not None else None,
        "breakevens": [str(price) for price in item.breakevens],
        "provisional_stop_per_option_unit":
        str(item.provisional_stop) if item.provisional_stop is not None else None,
        "provisional_target_per_option_unit":
        str(item.provisional_target) if item.provisional_target is not None else None,
        "fill_status": "NOT_ATTEMPTED",
        "frozen_in_study": (item.strategy_key, item.strategy_version)
        in (frozen_policy_ids or set()),
    } for item in assessment.candidates]
    return RecommendationSnapshot(
        decision="ABSTAIN", reason_code="INSUFFICIENT_EVIDENCE",
        selector_version=assessment.selector_version,
        evidence_snapshot={
            "kind": "NIFTY_MARKET_FIT_OPPORTUNITY_V1",
            "study_manifest_digest": study.manifest_digest,
            "policy_set_digest": policy_set_digest,
            "chain_digest": assessment.chain_digest,
            "chain_source": assessment.chain_source,
            "chain_provider_id": assessment.chain_provider_id,
            "chain_received_at": assessment.chain_received_at.isoformat()
            if assessment.chain_received_at else None,
            "chain_timestamp_basis": chain.timestamp_basis,
            "chain_as_of": as_of.isoformat(),
            "chain_expiry": chain.expiry.isoformat(),
            "reference_strike": (
                str(assessment.reference_strike)
                if assessment.reference_strike is not None else None
            ),
            "payoff_basis": assessment.payoff_basis,
            "bar_source": assessment.bar_source,
            "bar_provider_id": assessment.bar_provider_id,
            "bar_digest": assessment.bar_digest,
            "bar_count": assessment.bar_count,
            "bar_received_at": assessment.bar_received_at.isoformat()
            if assessment.bar_received_at else None,
            "bar_artifact_review": "UNREVIEWED_HASH_ONLY"
            if assessment.bar_digest is not None else "UNAVAILABLE",
            "chain_artifact_review": (
                "UNREVIEWED_SERVER_FETCHED"
                if assessment.chain_source == "SERVER_FETCHED_OPTION_CHAIN"
                else "UNREVIEWED_CLIENT_SUBMITTED"
            ),
            "cost_artifact_review": "UNREVIEWED_DECLARATION",
            "point_in_time_availability_review": "UNVERIFIED",
            "regime": assessment.regime,
            "realized_volatility_annualized_percent": (
                str(assessment.realized_volatility_annualized_percent)
                if assessment.realized_volatility_annualized_percent is not None else None
            ),
            "prior_20_bar_high": (
                str(assessment.prior_20_bar_high)
                if assessment.prior_20_bar_high is not None else None
            ),
            "prior_20_bar_low": (
                str(assessment.prior_20_bar_low)
                if assessment.prior_20_bar_low is not None else None
            ),
            "technical_level_status": "DERIVED_UNVALIDATED",
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
