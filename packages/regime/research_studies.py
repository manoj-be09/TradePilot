"""Immutable account-scoped parent study contracts."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.database.models import ResearchStudy
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


def study_view(row: ResearchStudy) -> dict[str, object]:
    return {"id": row.id, "study_key": row.study_key,
            "manifest": row.manifest, "manifest_digest": row.manifest_digest,
            "created_at": row.created_at, "promotion_eligible": False,
            "execution_enabled": False}
