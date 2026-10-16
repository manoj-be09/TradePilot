from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.research_routes import candidates, create_trial, record_result, trials
from packages.database.base import Base
from packages.database.models import ResearchCandidate, ResearchTrial, User
from packages.regime.candidate_registry import (
    CandidateDefinition,
    TrialManifest,
    TrialResult,
    declare_trial,
    finalize_trial,
    register_candidate,
)


def definition(**changes):
    return CandidateDefinition(**({"name": "Example trend hypothesis", "family": "trend",
        "market": "EQUITY", "hypothesis": "Test whether a completed trend persists after costs.",
        "source_url": "https://example.org/public-research", "source_license": "UNREVIEWED",
        "entry_rule": "Enter only on the next eligible session after a completed signal.",
        "exit_rule": "Exit using the frozen predefined risk policy.",
        "limitation": "Unverified hypothesis; not eligible for trading."} | changes))


def manifest(**changes):
    return TrialManifest(**({"dataset_digest": "a" * 64, "calculation_digest": "b" * 64,
        "cost_model_id": "illustrative-v1", "experiment_family": "trend-study-1",
        "trial_number": 1, "declared_search_budget": 5, "seed": 0,
        "train_start": "2020-01-01", "train_end": "2021-12-31",
        "validation_start": "2022-01-01", "validation_end": "2022-12-31",
        "holdout_start": "2023-01-01", "holdout_end": "2023-12-31"} | changes))


def test_registry_preserves_failures_replays_and_owner_boundaries():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    owner, other = uuid4(), uuid4()
    with Session(engine) as session:
        session.add_all([User(id=uid, email=f"{uid}@example.org", password_hash="fixture")
                         for uid in (owner, other)])
        session.commit()
        row = register_candidate(session, owner, definition())
        assert register_candidate(session, owner, definition()).id == row.id
        assert register_candidate(session, other, definition()).id != row.id
        attempt = declare_trial(session, owner, row.id, manifest())
        assert declare_trial(session, owner, row.id, manifest()).id == attempt.id
        assert attempt.result is None
        with pytest.raises(LookupError):
            declare_trial(session, other, row.id, manifest())
        failed = TrialResult(outcome="ERROR", reason_code="RUN_FAILED")
        result = finalize_trial(session, owner, attempt.id, failed)
        original_time = result.completed_at
        assert result.result["promotion_eligible"] is False
        assert result.result["evidence_origin"] == "OPERATOR_RECORDED"
        assert finalize_trial(session, owner, attempt.id, failed).completed_at == original_time
        with pytest.raises(ValueError, match="already finalized"):
            finalize_trial(session, owner, attempt.id, TrialResult(
                outcome="REPORTED", reason_code="COMPLETED", artifact_digest="c" * 64))
        with pytest.raises(HTTPException) as error:
            record_result(attempt.id, failed, SimpleNamespace(id=other), session)
        assert error.value.status_code == 404
        with pytest.raises(HTTPException) as error:
            create_trial(row.id, manifest(), SimpleNamespace(id=other), session)
        assert error.value.status_code == 404
        response = Response()
        saved = trials(row.id, SimpleNamespace(id=owner), session, response, limit=50, offset=0)
        assert len(saved) == 1
        assert saved[0]["result"]["outcome"] == "ERROR"
        assert response.headers["Cache-Control"] == "no-store"
        assert len(candidates(SimpleNamespace(id=owner), session, Response(), 50, 0)) == 1
        assert len(list(session.scalars(select(ResearchCandidate)))) == 2
        assert len(list(session.scalars(select(ResearchTrial)))) == 1
    engine.dispose()


@pytest.mark.parametrize("changes", [
    {"validation_start": "2021-12-31"}, {"holdout_start": "2022-06-01"},
    {"trial_number": 6}, {"dataset_digest": "not-a-digest"},
])
def test_manifest_rejects_overlap_bad_hash_or_over_budget_number(changes):
    with pytest.raises(ValidationError):
        manifest(**changes)


@pytest.mark.parametrize("url", ["http://example.org", "https://name:secret@example.org",
                                "https://example.org?token=secret"])
def test_source_rejects_credentials_and_nonpublic_reference_shape(url):
    with pytest.raises(ValidationError):
        definition(source_url=url)


def test_no_claimed_probability_or_unsupported_success():
    with pytest.raises(ValidationError):
        TrialResult(outcome="REPORTED", reason_code="COMPLETED")
    with pytest.raises(ValidationError):
        TrialResult(outcome="FAILED", reason_code="COMPLETED")
    with pytest.raises(ValidationError):
        TrialResult(outcome="ERROR", reason_code="RUN_FAILED", win_probability=0.99)


def test_registry_migration_adds_only_new_tables_and_preserves_existing_user():
    import runpy
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    engine = create_engine("sqlite://")
    old_tables = [table for name, table in Base.metadata.tables.items()
                  if name not in {"research_candidates", "research_trials"}]
    Base.metadata.create_all(engine, tables=old_tables)
    with engine.begin() as connection:
        connection.execute(User.__table__.insert().values(
            id=uuid4(), email="preserved@example.org", password_hash="fixture"))
        before = set(inspect(connection).get_table_names())
        migration = runpy.run_path(str(Path(__file__).resolve().parents[2]
                                       / "alembic/versions/0025_research_registry.py"))
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
        assert set(inspect(connection).get_table_names()) - before == {
            "research_candidates", "research_trials"}
        assert connection.scalar(text("SELECT count(*) FROM users")) == 1
        assert connection.scalar(text("SELECT email FROM users")) == "preserved@example.org"
    engine.dispose()


def test_registry_routes_require_existing_auth_dependency():
    from apps.api.auth import get_current_user
    from apps.api.research_routes import router

    assert len(router.routes) == 6
    for route in router.routes:
        assert any(item.call is get_current_user for item in route.dependant.dependencies)
