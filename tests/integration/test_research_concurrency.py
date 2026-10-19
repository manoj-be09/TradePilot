"""PostgreSQL acceptance for bounded research reservations and fencing."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.orm import sessionmaker

from packages.database.base import Base
from packages.database.models import (
    ResearchBudgetSlot,
    ResearchExperiment,
    ResearchTrial,
    User,
    WorkerLease,
)
from packages.regime.candidate_registry import (
    CandidateDefinition,
    TrialManifest,
    TrialResult,
    declare_trial,
    finalize_trial,
    register_candidate,
)
from packages.regime.research_budget import reserve_trial_slot
from packages.regime.research_scheduler_lease import (
    acquire_research_lease,
    assert_research_lease,
)


@pytest.fixture
def postgres_factory():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set; PostgreSQL is tested in CI")
    admin = create_engine(url)
    schema = "test_research_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _seed_research_trial(factory):
    owner = uuid4()
    with factory() as session:
        session.add(User(id=owner, email=f"{owner}@example.org", password_hash="fixture"))
        session.commit()
        candidate = register_candidate(session, owner, CandidateDefinition(
            name="Bounded research candidate",
            family="nifty-first-release",
            market="NIFTY_OPTIONS",
            hypothesis="Check a fixed rule without changing any trading settings.",
            source_url="https://example.org/research",
            source_license="UNREVIEWED",
            entry_rule="Use the predeclared fixed entry rule.",
            exit_rule="Use the predeclared fixed exit rule.",
            limitation="Integration fixture only; not strategy evidence.",
        ))
        trial = declare_trial(session, owner, candidate.id, TrialManifest(
            dataset_digest="a" * 64,
            calculation_digest="b" * 64,
            holdout_artifact_digest="c" * 64,
            cost_model_id="nifty-cost-v1",
            experiment_family="nifty-first-release",
            trial_number=1,
            declared_search_budget=2,
            seed=7,
            train_start="2020-01-01",
            train_end="2021-12-31",
            validation_start="2022-01-01",
            validation_end="2022-12-31",
            holdout_start="2023-01-01",
            holdout_end="2023-12-31",
        ))
        return owner, trial.id


def test_concurrent_research_slot_reservation_is_single_and_idempotent(postgres_factory):
    factory = postgres_factory
    owner, trial_id = _seed_research_trial(factory)
    barrier = Barrier(2)

    def reserve():
        with factory() as session:
            barrier.wait(timeout=10)
            return reserve_trial_slot(session, owner, trial_id).slot

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=20) for future in (
            pool.submit(reserve), pool.submit(reserve),
        )]

    assert results == [1, 1]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ResearchExperiment)) == 1
        assert session.scalar(select(func.count()).select_from(ResearchBudgetSlot)) == 1
        assert session.scalar(select(ResearchBudgetSlot.trial_id)) == trial_id


def test_postgres_research_lease_takeover_fences_stale_owner(postgres_factory):
    factory = postgres_factory
    with factory() as first:
        old = acquire_research_lease(first, "research-old", 60)
        assert old is not None and assert_research_lease(first, old)

    with factory() as second:
        lease = second.get(WorkerLease, "research-evaluator")
        assert lease is not None
        second.execute(update(WorkerLease).where(
            WorkerLease.name == "research-evaluator",
        ).values(lease_until=lease.lease_until - timedelta(seconds=120)))
        second.commit()
        current = acquire_research_lease(second, "research-new", 60)
        assert current is not None and current.generation > old.generation

    with factory() as stale:
        assert not assert_research_lease(stale, old)
        assert assert_research_lease(stale, current)


def test_concurrent_research_finalization_same_evidence_is_idempotent(postgres_factory):
    factory = postgres_factory
    owner, trial_id = _seed_research_trial(factory)
    result = TrialResult(outcome="ERROR", reason_code="RUN_FAILED")
    barrier = Barrier(2)

    def finalize():
        with factory() as session:
            barrier.wait(timeout=10)
            return finalize_trial(session, owner, trial_id, result).result_digest

    with ThreadPoolExecutor(max_workers=2) as pool:
        digests = [future.result(timeout=20) for future in (
            pool.submit(finalize), pool.submit(finalize),
        )]

    assert digests[0] == digests[1]
    with factory() as session:
        row = session.get(ResearchTrial, trial_id)
        assert row is not None and row.result_digest == digests[0]
