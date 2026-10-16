from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from packages.database.base import Base
from packages.database.models import ResearchBudgetSlot, User
from packages.regime.candidate_registry import (
    CandidateDefinition,
    TrialManifest,
    TrialResult,
    declare_trial,
    finalize_trial,
    register_candidate,
)
from packages.regime.research_budget import reserve_trial_slot


def test_durable_slots_share_budget_across_candidates_and_keep_failed_attempts(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'budget.sqlite'}")
    Base.metadata.create_all(engine)
    owner, other = uuid4(), uuid4()
    definition = CandidateDefinition(
        name="Fixed trend study", family="trend", market="EQUITY",
        hypothesis="Check a predefined trend rule without changing any trading settings.",
        source_url="https://example.org/study", source_license="UNREVIEWED",
        entry_rule="Use the existing fixed daily rule.", exit_rule="Use its existing exit policy.",
        limitation="Synthetic bookkeeping fixture, not profitable evidence.",
    )
    manifest = TrialManifest(
        dataset_digest="a" * 64, calculation_digest="b" * 64, cost_model_id="fixture-v1",
        experiment_family="one-study", trial_number=1, declared_search_budget=2, seed=0,
        train_start="2020-01-01", train_end="2021-12-31",
        validation_start="2022-01-01", validation_end="2022-12-31",
        holdout_start="2023-01-01", holdout_end="2023-12-31",
    )
    with Session(engine) as session:
        session.add_all([User(id=uid, email=f"{uid}@example.org", password_hash="fixture")
                         for uid in (owner, other)])
        session.commit()
        first = register_candidate(session, owner, definition)
        second = register_candidate(session, owner, CandidateDefinition.model_validate(
            definition.model_dump() | {"name": "Second fixed hypothesis"}))
        trial = declare_trial(session, owner, first.id, manifest)
        slot = reserve_trial_slot(session, owner, trial.id)
        family_id, trial_id = slot.experiment_id, trial.id
        assert reserve_trial_slot(session, owner, trial.id).slot == 1
        finalize_trial(session, owner, trial.id,
                       TrialResult(outcome="ERROR", reason_code="RUN_FAILED"))
        assert reserve_trial_slot(session, owner, trial.id).slot == 1
        with pytest.raises(LookupError):
            reserve_trial_slot(session, other, trial.id)
        duplicate = declare_trial(session, owner, second.id, manifest)
        with pytest.raises(ValueError, match="already consumed"):
            reserve_trial_slot(session, owner, duplicate.id)
        session.rollback()
        second_slot = declare_trial(session, owner, second.id, TrialManifest.model_validate(
            manifest.model_dump() | {"trial_number": 2}))
        assert reserve_trial_slot(session, owner, second_slot.id).slot == 2
        for changes in ({"declared_search_budget": 3, "trial_number": 3},
                        {"dataset_digest": "c" * 64}, {"holdout_end": "2024-01-01"}):
            conflicting = declare_trial(session, owner, first.id,
                TrialManifest.model_validate(manifest.model_dump() | changes))
            with pytest.raises(ValueError, match="budget or protected scope"):
                reserve_trial_slot(session, owner, conflicting.id)
            session.rollback()
        finished = declare_trial(session, owner, first.id, TrialManifest.model_validate(
            manifest.model_dump() | {"experiment_family": "legacy-finished"}))
        finalize_trial(session, owner, finished.id,
                       TrialResult(outcome="ERROR", reason_code="RUN_FAILED"))
        with pytest.raises(ValueError, match="after trial finalization"):
            reserve_trial_slot(session, owner, finished.id)
    with Session(engine) as session:
        slots = list(session.scalars(select(ResearchBudgetSlot)))
        assert len(slots) == 2
        assert session.get(ResearchBudgetSlot, (family_id, 1)).trial_id == trial_id
    engine.dispose()


def test_budget_and_evaluation_migrations_preserve_legacy_trials_without_backfill():
    import runpy
    from datetime import datetime, timezone
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    from packages.database.models import ResearchCandidate, ResearchTrial

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[table for name, table in Base.metadata.tables.items()
        if name not in {"research_experiments", "research_budget_slots", "research_evaluations"}])
    owner, candidate, trial = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(User.__table__.insert().values(
            id=owner, email="preserve@example.org", password_hash="fixture"))
        connection.execute(ResearchCandidate.__table__.insert().values(
            id=candidate, user_id=owner, definition_digest="a" * 64,
            definition={"legacy": True}, created_at=now))
        connection.execute(ResearchTrial.__table__.insert().values(
            id=trial, user_id=owner, candidate_id=candidate, manifest_digest="b" * 64,
            manifest={"legacy": "untouched"}, created_at=now))
        before = set(inspect(connection).get_table_names())
        migration = runpy.run_path(str(Path(__file__).resolve().parents[2]
                                       / "alembic/versions/0026_research_budget.py"))
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
        assert set(inspect(connection).get_table_names()) - before == {
            "research_experiments", "research_budget_slots"}
        assert connection.scalar(select(ResearchTrial.manifest).where(
            ResearchTrial.id == trial)) == {"legacy": "untouched"}
        assert list(connection.execute(select(ResearchBudgetSlot))) == []
        evaluation_migration = runpy.run_path(str(Path(__file__).resolve().parents[2]
            / "alembic/versions/0027_research_evaluation.py"))
        with Operations.context(MigrationContext.configure(connection)):
            evaluation_migration["upgrade"]()
        assert set(inspect(connection).get_table_names()) - before == {
            "research_experiments", "research_budget_slots", "research_evaluations"}
        assert connection.scalar(select(ResearchTrial.manifest).where(
            ResearchTrial.id == trial)) == {"legacy": "untouched"}
    engine.dispose()
