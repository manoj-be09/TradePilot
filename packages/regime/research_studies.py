"""Immutable account-scoped parent study contracts."""
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.database.models import ResearchStudy
from packages.regime.candidate_registry import fingerprint
from packages.regime.frozen_nifty_study import FrozenNiftyStudy


def freeze_nifty_study(session: Session, owner: UUID,
                       study: FrozenNiftyStudy) -> ResearchStudy:
    payload = study.manifest
    digest = study.manifest_digest
    existing = session.scalar(select(ResearchStudy).where(
        ResearchStudy.user_id == owner, ResearchStudy.study_key == study.study_key,
    ))
    if existing is not None:
        if existing.manifest_digest == digest:
            return existing
        raise ValueError("Study key already has a different immutable contract")
    row = ResearchStudy(user_id=owner, study_key=study.study_key,
                        manifest_digest=digest, manifest=payload,
                        created_at=datetime.now(timezone.utc))
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(ResearchStudy).where(
            ResearchStudy.user_id == owner, ResearchStudy.study_key == study.study_key,
        ))
        if existing is None or existing.manifest_digest != digest:
            raise
        return existing
    return row


def reject_overlapping_nifty_studies(
    session: Session, owner: UUID, dataset_digest: str,
    train_start: date, holdout_end: date,
) -> None:
    """Require review before a differently hashed study spends another parent slot."""
    for row in session.scalars(select(ResearchStudy).where(ResearchStudy.user_id == owner)):
        if fingerprint(row.manifest) != row.manifest_digest:
            raise ValueError("Overlapping study parent requires integrity review")
        earlier = FrozenNiftyStudy.model_validate(row.manifest)
        if (earlier.dataset_digest != dataset_digest
                and earlier.train_start <= holdout_end
                and train_start <= earlier.holdout_end):
            raise ValueError("Overlapping study datasets require canonical parent review")


def study_view(row: ResearchStudy) -> dict[str, object]:
    return {"id": row.id, "study_key": row.study_key,
            "manifest": row.manifest, "manifest_digest": row.manifest_digest,
            "created_at": row.created_at, "promotion_eligible": False,
            "execution_enabled": False}
