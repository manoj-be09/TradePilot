"""Authenticated hypothesis/trial bookkeeping, without execution or promotion."""
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select

from apps.api.auth import CurrentUser, SessionDep
from packages.database.models import ResearchCandidate, ResearchTrial
from packages.regime.candidate_registry import (
    CandidateDefinition,
    TrialManifest,
    TrialResult,
    declare_trial,
    finalize_trial,
    register_candidate,
)

router = APIRouter(prefix="/research", tags=["research registry"])


def candidate_view(row: ResearchCandidate) -> dict[str, Any]:
    return {"id": row.id, "definition": row.definition,
            "definition_digest": row.definition_digest, "created_at": row.created_at,
            "source_review": "UNREVIEWED", "execution_enabled": False}


def trial_view(row: ResearchTrial) -> dict[str, Any]:
    return {"id": row.id, "candidate_id": row.candidate_id, "manifest": row.manifest,
            "manifest_digest": row.manifest_digest, "result": row.result,
            "result_digest": row.result_digest, "created_at": row.created_at,
            "completed_at": row.completed_at, "promotion_eligible": False}


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
    return [trial_view(row) for row in session.scalars(select(ResearchTrial).where(
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
