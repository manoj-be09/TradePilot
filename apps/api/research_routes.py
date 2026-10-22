"""Authenticated hypothesis/trial bookkeeping, without execution or promotion."""
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from apps.api.auth import CurrentUser, SessionDep
from packages.backtesting.contracts import ScheduledOptionsBacktestResult
from packages.database.models import (
    BacktestRun,
    ResearchCandidate,
    ResearchEvaluation,
    ResearchEvidence,
    ResearchOpportunityEvent,
    ResearchPolicySet,
    ResearchRecommendation,
    ResearchStudy,
    ResearchTrial,
)
from packages.regime.candidate_registry import (
    CandidateDefinition,
    TrialManifest,
    TrialResult,
    declare_trial,
    finalize_trial,
    fingerprint,
    register_candidate,
)
from packages.regime.frozen_nifty_study import FrozenNiftyStudy
from packages.regime.registered_evaluator import record_registered_nifty_replay_job
from packages.regime.research_evidence import EvidenceRecord, record_evidence
from packages.regime.research_opportunity_events import (
    OpportunityEventInput,
    opportunity_event_view,
    record_opportunity_event,
)
from packages.regime.research_policy_sets import (
    FrozenPolicySet,
    freeze_policy_set,
    policy_set_view,
)
from packages.regime.research_readiness import assess_trial
from packages.regime.research_recommendations import (
    RecommendationSnapshot,
    build_abstention_recommendation,
    recommendation_view,
    record_recommendation,
)
from packages.regime.research_studies import freeze_nifty_study, study_view

router = APIRouter(prefix="/research", tags=["research registry"])


class NiftyReplayRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_id: UUID
    run_id: UUID
    lease_seconds: int = Field(default=120, ge=30, le=600)


class TrialRecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selector_version: str = Field(min_length=1, max_length=64)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def utc_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Recommendation expiry must include a timezone")
        return value.astimezone(timezone.utc)


class ManualAbstentionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def utc_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Recommendation expiry must include a timezone")
        return value.astimezone(timezone.utc)


@router.post("/studies")
def create_study(body: FrozenNiftyStudy, user: CurrentUser,
                session: SessionDep) -> dict[str, Any]:
    try:
        return study_view(freeze_nifty_study(session, user.id, body))
    except ValueError:
        raise HTTPException(409, "Study key already has a different immutable contract") from None


@router.get("/studies")
def studies(user: CurrentUser, session: SessionDep, response: Response,
            limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)
            ) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "no-store"
    query = (select(ResearchStudy).where(ResearchStudy.user_id == user.id)
             .order_by(ResearchStudy.created_at.desc(), ResearchStudy.id.desc())
             .offset(offset).limit(limit))
    return [study_view(row) for row in session.scalars(query)]


@router.post("/studies/{study_id}/policy-set")
def create_policy_set(study_id: UUID, body: FrozenPolicySet,
                      user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    try:
        return policy_set_view(freeze_policy_set(session, user.id, study_id, body))
    except LookupError:
        raise HTTPException(404, "Study not found") from None
    except ValueError as error:
        raise HTTPException(409, str(error)) from None


@router.get("/studies/{study_id}/policy-set")
def get_policy_set(study_id: UUID, user: CurrentUser,
                   session: SessionDep, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    row = session.scalar(select(ResearchPolicySet).where(
        ResearchPolicySet.study_id == study_id, ResearchPolicySet.user_id == user.id,
    ))
    if row is None:
        raise HTTPException(404, "Policy set not found")
    return policy_set_view(row)


@router.post("/studies/{study_id}/recommendations")
def create_recommendation(study_id: UUID, body: ManualAbstentionRequest,
                          user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Record an operator abstention without accepting client-authored evidence claims."""
    study = session.scalar(select(ResearchStudy).where(
        ResearchStudy.id == study_id, ResearchStudy.user_id == user.id,
    ))
    if study is None:
        raise HTTPException(404, "Study not found") from None
    try:
        frozen = FrozenNiftyStudy.model_validate(study.manifest)
    except ValueError:
        raise HTTPException(409, "Study manifest requires review") from None
    if frozen.manifest_digest != study.manifest_digest:
        raise HTTPException(409, "Study manifest requires review")
    review_days = frozen.review_interval_days
    now = datetime.now(timezone.utc)
    if not now < body.expires_at <= now + timedelta(days=review_days, minutes=1):
        raise HTTPException(422, "Manual abstention expiry exceeds the study review interval")
    snapshot = RecommendationSnapshot(
        decision="ABSTAIN", reason_code="INSUFFICIENT_EVIDENCE",
        selector_version="manual-abstention-v1",
        evidence_snapshot={
            "kind": "MANUAL_ABSTENTION_V1",
            "evidence_status": "UNREVIEWED",
            "study_manifest_digest": study.manifest_digest,
        },
        rejected_reasons=["No strategy has sufficient reviewed evidence for selection."],
        risk_context={"execution_enabled": False, "promotion_eligible": False},
        expires_at=body.expires_at,
    )
    return recommendation_view(record_recommendation(session, user.id, study_id, snapshot))


@router.get("/studies/{study_id}/recommendations")
def recommendations(study_id: UUID, user: CurrentUser, session: SessionDep,
                    response: Response, limit: int = Query(25, ge=1, le=100),
                    offset: int = Query(0, ge=0)) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "no-store"
    if session.scalar(select(ResearchStudy.id).where(
        ResearchStudy.id == study_id, ResearchStudy.user_id == user.id,
    )) is None:
        raise HTTPException(404, "Study not found")
    return [recommendation_view(row) for row in session.scalars(select(
        ResearchRecommendation,
    ).where(
        ResearchRecommendation.user_id == user.id,
        ResearchRecommendation.study_id == study_id,
    ).order_by(ResearchRecommendation.created_at.desc(), ResearchRecommendation.id.desc())
      .offset(offset).limit(limit))]


@router.post("/opportunities/{recommendation_id}/events")
def create_opportunity_event(recommendation_id: UUID, body: OpportunityEventInput,
                             user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    try:
        return opportunity_event_view(record_opportunity_event(
            session, user.id, recommendation_id, body))
    except LookupError:
        raise HTTPException(404, "Opportunity not found") from None
    except ValueError as error:
        raise HTTPException(409, str(error)) from None


@router.get("/opportunities/{recommendation_id}/events")
def opportunity_events(recommendation_id: UUID, user: CurrentUser,
                       session: SessionDep, response: Response,
                       limit: int = Query(25, ge=1, le=100),
                       offset: int = Query(0, ge=0)) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "no-store"
    if session.scalar(select(ResearchRecommendation.id).where(
        ResearchRecommendation.id == recommendation_id,
        ResearchRecommendation.user_id == user.id,
    )) is None:
        raise HTTPException(404, "Opportunity not found")
    rows = session.scalars(select(ResearchOpportunityEvent).where(
        ResearchOpportunityEvent.recommendation_id == recommendation_id,
        ResearchOpportunityEvent.user_id == user.id,
    ).order_by(ResearchOpportunityEvent.created_at.desc(),
               ResearchOpportunityEvent.id.desc()).offset(offset).limit(limit))
    return [opportunity_event_view(row) for row in rows]


@router.post("/studies/{study_id}/recommendations/from-trial/{trial_id}")
def recommend_from_trial(study_id: UUID, trial_id: UUID,
                         body: TrialRecommendationRequest, user: CurrentUser,
                         session: SessionDep) -> dict[str, Any]:
    """Record a deterministic research-only abstention from stored evidence."""
    study = session.scalar(select(ResearchStudy).where(
        ResearchStudy.id == study_id, ResearchStudy.user_id == user.id,
    ))
    trial = session.scalar(select(ResearchTrial).where(
        ResearchTrial.id == trial_id, ResearchTrial.user_id == user.id,
    ))
    if study is None or trial is None:
        raise HTTPException(404, "Study or trial not found")
    candidate = session.scalar(select(ResearchCandidate).where(
        ResearchCandidate.id == trial.candidate_id, ResearchCandidate.user_id == user.id,
    ))
    if candidate is None or candidate.definition.get("market") != "NIFTY_OPTIONS":
        raise HTTPException(409, "Only an owned NIFTY trial can be reviewed for this study")
    try:
        frozen = FrozenNiftyStudy.model_validate(study.manifest)
        manifest = TrialManifest.model_validate(trial.manifest)
        if (frozen.manifest_digest != study.manifest_digest
                or fingerprint(trial.manifest) != trial.manifest_digest
                or manifest.experiment_family != frozen.study_key):
            raise ValueError("Trial or study identity differs from the frozen NIFTY parent")
        for key in ("rights_artifact_digest", "dataset_digest", "calculation_digest",
                    "holdout_artifact_digest",
                    "cost_model_id", "cost_artifact_digest", "validation_end",
                    "holdout_start", "holdout_end"):
            if getattr(manifest, key) != getattr(frozen, key):
                raise ValueError("Trial manifest differs from the frozen NIFTY study")
        if manifest.declared_search_budget != frozen.parent_search_budget:
            raise ValueError("Trial search budget differs from the frozen NIFTY parent budget")
    except ValueError:
        raise HTTPException(409, "Trial is outside the frozen NIFTY study") from None
    evaluation = session.get(ResearchEvaluation, trial.id)
    evidence = list(session.scalars(select(ResearchEvidence).where(
        ResearchEvidence.trial_id == trial.id).order_by(ResearchEvidence.key)))
    assessment = assess_trial(trial, evaluation, evidence)
    snapshot = build_abstention_recommendation(
        assessment, selector_version=body.selector_version, expires_at=body.expires_at,
    ).model_copy(update={"candidate_id": trial.candidate_id, "trial_id": trial.id})
    return recommendation_view(record_recommendation(session, user.id, study_id, snapshot))


def candidate_view(row: ResearchCandidate) -> dict[str, Any]:
    return {"id": row.id, "definition": row.definition,
            "definition_digest": row.definition_digest, "created_at": row.created_at,
            "source_review": "UNREVIEWED", "execution_enabled": False}


def trial_view(row: ResearchTrial, evaluation: ResearchEvaluation | None = None,
               evidence: list[ResearchEvidence] | None = None) -> dict[str, Any]:
    return {"id": row.id, "candidate_id": row.candidate_id, "manifest": row.manifest,
            "manifest_digest": row.manifest_digest, "result": row.result,
            "result_digest": row.result_digest, "created_at": row.created_at,
            "completed_at": row.completed_at, "promotion_eligible": False,
            "readiness": assess_trial(row, evaluation, evidence).model_dump(mode="json"),
            "evidence": [{"id": item.id, "key": item.key, "status": item.status,
                          "detail": item.detail, "artifact_ref": item.artifact_ref,
                          "artifact_digest": item.artifact_digest,
                          "artifact_version": item.artifact_version,
                          "recorded_at": item.recorded_at, "received_at": item.created_at,
                          "allowed_use": item.allowed_use}
                        for item in (evidence or [])],
            "engine_evaluation": None if evaluation is None else {
                "adapter": evaluation.adapter, "adapter_digest": evaluation.adapter_digest,
                "result": evaluation.result, "result_digest": evaluation.result_digest,
                "created_at": evaluation.created_at, "completed_at": evaluation.completed_at,
                "binding_review": "UNREVIEWED", "promotion_eligible": False,
            }}


@router.post("/candidates")
def create_candidate(body: CandidateDefinition, user: CurrentUser,
                     session: SessionDep) -> dict[str, Any]:
    return candidate_view(register_candidate(session, user.id, body))


@router.get("/candidates")
def candidates(user: CurrentUser, session: SessionDep, response: Response,
               limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
               ) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "no-store"
    query = (select(ResearchCandidate).where(ResearchCandidate.user_id == user.id)
             .order_by(ResearchCandidate.created_at.desc(), ResearchCandidate.id.desc())
             .offset(offset).limit(limit))
    return [candidate_view(row) for row in session.scalars(query)]


@router.post("/candidates/{candidate_id}/trials")
def create_trial(candidate_id: UUID, body: TrialManifest, user: CurrentUser,
                 session: SessionDep) -> dict[str, Any]:
    try:
        return trial_view(declare_trial(session, user.id, candidate_id, body))
    except LookupError:
        raise HTTPException(404, "Candidate not found") from None


@router.get("/candidates/{candidate_id}/trials")
def trials(candidate_id: UUID, user: CurrentUser, session: SessionDep, response: Response,
           limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
           ) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "no-store"
    if session.scalar(select(ResearchCandidate.id).where(
        ResearchCandidate.id == candidate_id, ResearchCandidate.user_id == user.id,
    )) is None:
        raise HTTPException(404, "Candidate not found")
    query = (select(ResearchTrial, ResearchEvaluation).outerjoin(
        ResearchEvaluation, ResearchEvaluation.trial_id == ResearchTrial.id).where(
        ResearchTrial.user_id == user.id, ResearchTrial.candidate_id == candidate_id,
    ).order_by(ResearchTrial.created_at.desc(), ResearchTrial.id.desc())
             .offset(offset).limit(limit))
    return [trial_view(row, evaluation, list(session.scalars(select(ResearchEvidence).where(
            ResearchEvidence.trial_id == row.id).order_by(ResearchEvidence.key))))
            for row, evaluation in session.execute(query)]


@router.post("/trials/{trial_id}/nifty-replay")
def record_nifty_replay(trial_id: UUID, body: NiftyReplayRecordRequest,
                        user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Attach an existing stored scheduled replay to a frozen NIFTY trial.

    This endpoint never fetches market data or runs a provider. It only reads
    the caller's own backtest run, validates it against the frozen study and
    records an unqualified engine result with promotion disabled.
    """
    trial = session.scalar(select(ResearchTrial).where(
        ResearchTrial.id == trial_id, ResearchTrial.user_id == user.id,
    ))
    if trial is None:
        raise HTTPException(404, "Trial not found")
    study_row = session.scalar(select(ResearchStudy).where(
        ResearchStudy.id == body.study_id, ResearchStudy.user_id == user.id,
    ))
    if study_row is None:
        raise HTTPException(404, "Study not found")
    run = session.scalar(select(BacktestRun).where(
        BacktestRun.id == body.run_id, BacktestRun.user_id == user.id,
    ))
    if run is None:
        raise HTTPException(404, "Backtest run not found")
    try:
        replay = ScheduledOptionsBacktestResult.model_validate(run.result_data)
        study = FrozenNiftyStudy.model_validate(study_row.manifest)
        outcome, saved = record_registered_nifty_replay_job(
            session, user.id, trial_id, study, replay,
            job_owner=f"research-api:{user.id}", lease_seconds=body.lease_seconds,
        )
    except ValueError:
        raise HTTPException(409, "Stored replay does not match the frozen NIFTY study") from None
    if outcome.status != "COMPLETED" or saved is None:
        raise HTTPException(409, outcome.detail)
    return trial_view(trial, saved, list(session.scalars(select(ResearchEvidence).where(
        ResearchEvidence.trial_id == trial.id).order_by(ResearchEvidence.key))))


@router.get("/trials")
def all_trials(user: CurrentUser, session: SessionDep, response: Response,
               limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
               ) -> list[dict[str, Any]]:
    """List an owner's trials across candidates for comparison only."""
    response.headers["Cache-Control"] = "no-store"
    rows = session.execute(
        select(ResearchTrial, ResearchEvaluation, ResearchCandidate).outerjoin(
            ResearchEvaluation, ResearchEvaluation.trial_id == ResearchTrial.id,
        ).join(
            ResearchCandidate, ResearchCandidate.id == ResearchTrial.candidate_id,
        ).where(
            ResearchTrial.user_id == user.id,
            ResearchCandidate.user_id == user.id,
        ).order_by(ResearchTrial.created_at.desc(), ResearchTrial.id.desc())
        .offset(offset).limit(limit)
    )
    result = []
    for trial, evaluation, candidate in rows:
        evidence = list(session.scalars(select(ResearchEvidence).where(
            ResearchEvidence.trial_id == trial.id).order_by(ResearchEvidence.key)))
        item = trial_view(trial, evaluation, evidence)
        item["candidate"] = candidate.definition
        result.append(item)
    return result


@router.post("/trials/{trial_id}/result")
def record_result(trial_id: UUID, body: TrialResult, user: CurrentUser,
                  session: SessionDep) -> dict[str, Any]:
    try:
        return trial_view(finalize_trial(session, user.id, trial_id, body))
    except LookupError:
        raise HTTPException(404, "Trial not found") from None
    except ValueError:
        raise HTTPException(409, "Trial already finalized with different evidence") from None


@router.post("/trials/{trial_id}/evidence")
def record_trial_evidence(trial_id: UUID, body: EvidenceRecord, user: CurrentUser,
                          session: SessionDep) -> dict[str, Any]:
    try:
        row = record_evidence(session, user.id, trial_id, body)
    except LookupError:
        raise HTTPException(404, "Trial not found") from None
    except ValueError:
        raise HTTPException(409, "Evidence key already has different immutable evidence") from None
    return {"id": row.id, "trial_id": row.trial_id, "key": row.key, "status": row.status,
            "detail": row.detail, "artifact_ref": row.artifact_ref,
            "artifact_digest": row.artifact_digest, "artifact_version": row.artifact_version,
            "recorded_at": row.recorded_at, "allowed_use": row.allowed_use,
            "evidence_digest": row.evidence_digest}
