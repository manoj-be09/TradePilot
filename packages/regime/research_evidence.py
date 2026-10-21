"""Account-scoped, immutable evidence references for readiness review."""
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.database.models import ResearchEvidence, ResearchTrial
from packages.regime.candidate_registry import fingerprint

EvidenceKey = Literal["rights", "data", "costs", "binding", "holdout", "budget", "runtime"]
EvidenceStatus = Literal["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"]
EvidenceUse = Literal["INSPECT_RESEARCH_RECORDS", "RESEARCH_ONLY", "QUALIFIED_PAPER"]


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: EvidenceKey
    status: EvidenceStatus
    detail: str = Field(min_length=10, max_length=1000)
    artifact_ref: str = Field(min_length=1, max_length=500)
    artifact_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    artifact_version: str = Field(min_length=1, max_length=128)
    recorded_at: datetime
    allowed_use: EvidenceUse

    @field_validator("artifact_ref")
    @classmethod
    def public_reference(cls, value: str) -> str:
        if value != value.strip() or any(char in value for char in "\r\n\t"):
            raise ValueError("Artifact reference cannot contain whitespace controls")
        parsed = urlsplit(value)
        if (parsed.scheme not in {"https", "artifact"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or (parsed.scheme == "https" and not parsed.hostname)
                or (parsed.scheme == "artifact" and not (parsed.netloc or parsed.path))):
            raise ValueError(
                "Use an HTTPS or artifact reference without credentials, query or fragment"
            )
        return value

    @field_validator("recorded_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Evidence time must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def digest_for_reviewed_status(self) -> "EvidenceRecord":
        if self.status in {"PASS", "FAIL"} and self.artifact_digest is None:
            raise ValueError("Reviewed evidence requires an artifact digest")
        if self.allowed_use == "QUALIFIED_PAPER" and self.status != "PASS":
            raise ValueError("Qualified paper use requires a passing evidence check")
        return self


def persisted_evidence_digest(row: ResearchEvidence) -> str | None:
    """Recompute an evidence row's canonical digest before readiness trusts it."""
    recorded_at = row.recorded_at
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    try:
        record = EvidenceRecord(
            key=row.key, status=row.status, detail=row.detail, artifact_ref=row.artifact_ref,
            artifact_digest=row.artifact_digest, artifact_version=row.artifact_version,
            recorded_at=recorded_at, allowed_use=row.allowed_use,
        )
    except (ValidationError, ValueError):
        return None
    return fingerprint(record.model_dump(mode="json"))


def record_evidence(session: Session, owner: UUID, trial_id: UUID,
                    evidence: EvidenceRecord) -> ResearchEvidence:
    """Persist one immutable check per trial/key; same payload is idempotent."""
    trial = session.scalar(select(ResearchTrial).where(
        ResearchTrial.id == trial_id, ResearchTrial.user_id == owner,
    ))
    if trial is None:
        raise LookupError("Trial not found")
    payload = evidence.model_dump(mode="json")
    digest = fingerprint(payload)
    existing = session.scalar(select(ResearchEvidence).where(
        ResearchEvidence.trial_id == trial_id, ResearchEvidence.key == evidence.key,
    ))
    if existing is not None:
        if existing.evidence_digest == digest:
            return existing
        raise ValueError("Evidence key already has a different immutable record")
    row = ResearchEvidence(
        user_id=owner, trial_id=trial_id, key=evidence.key, status=evidence.status,
        detail=evidence.detail, artifact_ref=evidence.artifact_ref,
        artifact_digest=evidence.artifact_digest, artifact_version=evidence.artifact_version,
        recorded_at=evidence.recorded_at, allowed_use=evidence.allowed_use,
        evidence_digest=digest, created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(ResearchEvidence).where(
            ResearchEvidence.trial_id == trial_id, ResearchEvidence.key == evidence.key,
        ))
        if existing is None or existing.evidence_digest != digest:
            raise
        return existing
    return row
