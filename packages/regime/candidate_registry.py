"""Research bookkeeping only; no data fetching, execution, ranking or promotion."""
import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.database.models import ResearchCandidate, ResearchTrial


class CandidateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=3, max_length=120)
    family: str = Field(min_length=3, max_length=64)
    market: Literal["EQUITY", "NIFTY_OPTIONS"]
    hypothesis: str = Field(min_length=20, max_length=2000)
    source_url: str = Field(max_length=500)
    source_license: str = Field(min_length=3, max_length=200)
    entry_rule: str = Field(min_length=10, max_length=2000)
    exit_rule: str = Field(min_length=10, max_length=2000)
    limitation: str = Field(min_length=10, max_length=1000)

    @field_validator("source_url")
    @classmethod
    def public_source(cls, value: str) -> str:
        url = urlsplit(value)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment):
            raise ValueError("Use a public HTTPS reference without credentials, query or fragment")
        return value


class TrialManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    calculation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    cost_model_id: str = Field(min_length=3, max_length=100)
    experiment_family: str = Field(min_length=3, max_length=100)
    trial_number: int = Field(ge=1, le=10000)
    declared_search_budget: int = Field(ge=1, le=10000)
    seed: int = Field(ge=0, le=2147483647)
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    holdout_start: date
    holdout_end: date

    @model_validator(mode="after")
    def chronology(self) -> "TrialManifest":
        if not (self.train_start <= self.train_end < self.validation_start
                <= self.validation_end < self.holdout_start <= self.holdout_end):
            raise ValueError("Research periods must be chronological and non-overlapping")
        if self.trial_number > self.declared_search_budget:
            raise ValueError("Trial number exceeds the declared search budget")
        return self


class TrialResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["REPORTED", "FAILED", "INSUFFICIENT", "ERROR"]
    reason_code: Literal["COMPLETED", "DATA_UNAVAILABLE", "INVALID_RULE", "CHECK_FAILED",
                         "INSUFFICIENT_SAMPLE", "RUN_FAILED"]
    artifact_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    @model_validator(mode="after")
    def consistent_outcome(self) -> "TrialResult":
        allowed = {"REPORTED": {"COMPLETED"}, "FAILED": {"INVALID_RULE", "CHECK_FAILED"},
                   "INSUFFICIENT": {"DATA_UNAVAILABLE", "INSUFFICIENT_SAMPLE"},
                   "ERROR": {"RUN_FAILED"}}
        if self.reason_code not in allowed[self.outcome]:
            raise ValueError("Outcome and reason code disagree")
        if self.outcome == "REPORTED" and self.artifact_digest is None:
            raise ValueError("Reported completion requires an artifact digest")
        return self

    # No arbitrary error text, claimed probabilities, or supplied execution eligibility.


def fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def register_candidate(session: Session, owner: UUID,
                       definition: CandidateDefinition) -> ResearchCandidate:
    payload = definition.model_dump(mode="json")
    digest = fingerprint(payload)
    query = select(ResearchCandidate).where(ResearchCandidate.user_id == owner,
                                           ResearchCandidate.definition_digest == digest)
    existing = session.scalar(query)
    if existing is not None:
        return existing
    row = ResearchCandidate(user_id=owner, definition=payload, definition_digest=digest,
                            created_at=datetime.now(timezone.utc))
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(query)
        if existing is None:
            raise
        return existing
    return row


def declare_trial(session: Session, owner: UUID, candidate_id: UUID,
                  manifest: TrialManifest) -> ResearchTrial:
    if session.scalar(select(ResearchCandidate.id).where(
        ResearchCandidate.id == candidate_id, ResearchCandidate.user_id == owner,
    )) is None:
        raise LookupError("Candidate not found")
    payload = manifest.model_dump(mode="json")
    digest = fingerprint(payload)
    query = select(ResearchTrial).where(ResearchTrial.user_id == owner,
        ResearchTrial.candidate_id == candidate_id, ResearchTrial.manifest_digest == digest)
    existing = session.scalar(query)
    if existing is not None:
        return existing
    row = ResearchTrial(user_id=owner, candidate_id=candidate_id, manifest=payload,
                        manifest_digest=digest, created_at=datetime.now(timezone.utc))
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(query)
        if existing is None:
            raise
        return existing
    return row


def finalize_trial(session: Session, owner: UUID, trial_id: UUID,
                   result: TrialResult) -> ResearchTrial:
    payload = result.model_dump(mode="json") | {"evidence_origin": "OPERATOR_RECORDED",
                                               "promotion_eligible": False}
    digest = fingerprint(payload)
    session.execute(update(ResearchTrial).where(
        ResearchTrial.id == trial_id, ResearchTrial.user_id == owner,
        ResearchTrial.result_digest.is_(None),
    ).values(result=payload, result_digest=digest, completed_at=datetime.now(timezone.utc)))
    session.commit()
    row = session.scalar(select(ResearchTrial).where(
        ResearchTrial.id == trial_id, ResearchTrial.user_id == owner,
    ).execution_options(populate_existing=True))
    if row is None:
        raise LookupError("Trial not found")
    if row.result_digest != digest:
        raise ValueError("Trial already finalized with different evidence")
    return row
