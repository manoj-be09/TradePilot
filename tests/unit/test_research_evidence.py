from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.research_routes import record_trial_evidence, trials
from packages.database.base import Base
from packages.database.models import ResearchCandidate, ResearchEvidence, ResearchTrial, User
from packages.regime.candidate_registry import fingerprint
from packages.regime.research_evidence import EvidenceRecord, record_evidence
from packages.regime.research_readiness import assess_trial


def _fixture():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner, candidate, trial = uuid4(), uuid4(), uuid4()
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    with Session(engine) as session:
        session.add(User(id=owner, email="evidence@example.org", password_hash="fixture"))
        session.add(ResearchCandidate(id=candidate, user_id=owner, definition_digest="a" * 64,
                                      definition={"name": "fixture"}, created_at=now))
        session.add(ResearchTrial(id=trial, user_id=owner, candidate_id=candidate,
                                  manifest_digest="b" * 64, manifest={"fixture": True},
                                  created_at=now))
        session.commit()
    return engine, owner, candidate, trial


def evidence(**changes):
    return EvidenceRecord(**({
        "key": "data", "status": "PASS", "detail": "Reviewed source bytes and session coverage.",
        "artifact_ref": "artifact:nifty-daily-2026", "artifact_digest": "c" * 64,
        "artifact_version": "nifty-daily-v1", "recorded_at": "2026-01-02T10:00:00+05:30",
        "allowed_use": "RESEARCH_ONLY",
    } | changes))


def test_evidence_is_immutable_idempotent_and_appears_in_readiness():
    engine, owner, _, trial = _fixture()
    with Session(engine) as session:
        saved = record_evidence(session, owner, trial, evidence())
        assert record_evidence(session, owner, trial, evidence()).id == saved.id
        with pytest.raises(ValueError, match="different immutable"):
            record_evidence(session, owner, trial, evidence(detail="A different reviewed finding."))
        row = session.get(ResearchTrial, trial)
        assessment = assess_trial(row, evidence=list(session.scalars(select(ResearchEvidence))))
        check = next(item for item in assessment.checks if item.key == "data")
        assert check.status == "PASS"
        assert check.artifact_version == "nifty-daily-v1"
        assert check.allowed_use == "RESEARCH_ONLY"
        assert check.evidence_recorded_at.isoformat() == "2026-01-02T04:30:00+00:00"
    engine.dispose()


@pytest.mark.parametrize("changes", [
    {"status": "PASS", "artifact_digest": None},
    {"allowed_use": "QUALIFIED_PAPER", "status": "UNKNOWN", "artifact_digest": None},
    {"recorded_at": "2026-01-02T10:00:00"},
])
def test_evidence_rejects_unqualified_or_ambiguous_records(changes):
    with pytest.raises(ValidationError):
        evidence(**changes)


@pytest.mark.parametrize("reference", [
    "artifact://user:secret@dataset/2026",
    "artifact://dataset/2026?token=secret",
    "artifact:dataset-2026#secret",
    "https://example.org/data?token=secret",
    "https://user:secret@example.org/data",
    "relative/path?token=secret",
    "artifact:dataset-2026\nsecret",
])
def test_evidence_rejects_private_or_ambiguous_artifact_references(reference):
    with pytest.raises(ValidationError):
        evidence(artifact_ref=reference)


def test_evidence_accepts_opaque_artifact_and_public_https_references():
    assert evidence(artifact_ref="artifact://dataset/2026").artifact_ref == (
        "artifact://dataset/2026")
    assert evidence(artifact_ref="https://example.org/data/2026").artifact_ref == (
        "https://example.org/data/2026")


def test_evidence_route_is_owner_scoped_and_exposes_digest():
    engine, owner, candidate, trial = _fixture()
    with Session(engine) as session:
        response = record_trial_evidence(trial, evidence(), SimpleNamespace(id=owner), session)
        assert response["evidence_digest"] == fingerprint(evidence().model_dump(mode="json"))
        rows = trials(candidate, SimpleNamespace(id=owner), session, SimpleNamespace(headers={}),
                      limit=50, offset=0)
        assert rows[0]["evidence"][0]["artifact_version"] == "nifty-daily-v1"
    engine.dispose()


def test_readiness_rejects_mutated_evidence_row():
    engine, owner, _, trial = _fixture()
    with Session(engine) as session:
        saved = record_evidence(session, owner, trial, evidence())
        saved.detail = "Tampered after the immutable record was written."
        session.flush()
        row = session.get(ResearchTrial, trial)
        assessment = assess_trial(row, evidence=list(session.scalars(select(ResearchEvidence))))
        check = next(item for item in assessment.checks if item.key == "data")
        assert check.status == "FAIL"
        assert "immutable evidence digest" in check.detail
    engine.dispose()

