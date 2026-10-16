"""Authenticated hypothesis/trial bookkeeping, without execution or promotion."""
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select

from apps.api.auth import CurrentUser, SessionDep
from packages.database.models import (
    ResearchCandidate,
    ResearchEvaluation,
    ResearchEvidence,
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
    register_candidate,
)
from packages.regime.frozen_nifty_study import FrozenNiftyStudy
from packages.regime.research_evidence import EvidenceRecord, record_evidence
from packages.regime.research_readiness import assess_trial
from packages.regime.research_recommendations import (
    RecommendationSnapshot,
    recommendation_view,
    record_recommendation,
)
from packages.regime.research_studies import freeze_nifty_study, study_view

router = APIRouter(prefix="/research", tags=["research registry"])


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
    return [study_view(row) for row in session.scalars(select(ResearchStudy).where(
        ResearchStudy.user_id == user.id,
    ).order_by(ResearchStudy.created_at, ResearchStudy.id).offset(offset).limit(limit))]


@router.post("/studies/{study_id}/recommendations")
def create_recommendation(study_id: UUID, body: RecommendationSnapshot,
                          user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    try:
        return recommendation_view(record_recommendation(session, user.id, study_id, body))
    except LookupError:
        raise HTTPException(404, "Study not found") from None


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
    ).order_by(ResearchRecommendation.created_at, ResearchRecommendation.id)
      .offset(offset).limit(limit))]


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
                          "recorded_at": item.recorded_at, "allowed_use": item.allowed_use}
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
    return [candidate_view(row) for row in session.scalars(select(ResearchCandidate).where(
        ResearchCandidate.user_id == user.id,
    ).order_by(ResearchCandidate.created_at, ResearchCandidate.id).offset(offset).limit(limit))]


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
    return [trial_view(row, evaluation, list(session.scalars(select(ResearchEvidence).where(
            ResearchEvidence.trial_id == row.id).order_by(ResearchEvidence.key))))
            for row, evaluation in session.execute(
        select(ResearchTrial, ResearchEvaluation).outerjoin(ResearchEvaluation,
            ResearchEvaluation.trial_id == ResearchTrial.id).where(
        ResearchTrial.user_id == user.id, ResearchTrial.candidate_id == candidate_id,
    ).order_by(ResearchTrial.created_at, ResearchTrial.id).offset(offset).limit(limit))]


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
