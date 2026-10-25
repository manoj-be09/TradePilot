from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from packages.database.base import Base
from packages.database.models import WorkerLease
from packages.regime import research_scheduler_lease
from packages.regime.research_scheduler_lease import (
    RESEARCH_WORKER_NAME,
    ResearchJobCancelled,
    ResearchJobStale,
    acquire_research_lease,
    assert_research_lease,
    finish_research_lease,
    renew_research_lease,
    run_research_job,
)


def test_research_lease_fences_takeover_and_stale_finish():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        old = acquire_research_lease(session, "old-research", 60)
        assert old is not None and assert_research_lease(session, old)
        assert renew_research_lease(session, old, 120)
        current = session.get(WorkerLease, RESEARCH_WORKER_NAME)
        assert current is not None
        session.execute(update(WorkerLease).where(
            WorkerLease.name == RESEARCH_WORKER_NAME,
        ).values(lease_until=current.lease_until - timedelta(seconds=120)))
        session.commit()
        new = acquire_research_lease(session, "new-research", 60)
        assert new is not None and new.generation > old.generation
        assert not assert_research_lease(session, old)
        assert not renew_research_lease(session, old, 120)
        assert assert_research_lease(session, new)
        finish_research_lease(session, old, "ERROR", "stale result")
        current = session.get(WorkerLease, RESEARCH_WORKER_NAME)
        assert current is not None and current.owner == "new-research"
        finish_research_lease(session, new, "HEALTHY", "review cycle complete")
        assert not assert_research_lease(session, new)
    engine.dispose()


def test_research_job_runner_handles_completion_busy_and_failure():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        calls: list[str] = []
        completed = run_research_job(session, "research-one", 60,
                                     lambda _token: calls.append("ran"))
        assert completed.status == "COMPLETED"
        assert calls == ["ran"]

        held = acquire_research_lease(session, "research-held", 60)
        assert held is not None
        skipped = run_research_job(session, "research-two", 60,
                                   lambda _token: calls.append("wrong-worker"))
        assert skipped.status == "SKIPPED"
        assert calls == ["ran"]
        finish_research_lease(session, held, "HEALTHY", "released")

        failed = run_research_job(
            session, "research-three", 60,
            lambda _token: (_ for _ in ()).throw(RuntimeError("secret detail")),
        )
        assert failed.status == "FAILED"
        lease = session.get(WorkerLease, RESEARCH_WORKER_NAME)
        assert lease is not None and lease.status == "DEGRADED"
        assert "secret detail" not in lease.detail

        def lose_lease(_token) -> None:
            raise ResearchJobStale()

        stale = run_research_job(session, "research-stale-task", 60, lose_lease)
        assert stale.status == "STALE"
        lease = session.get(WorkerLease, RESEARCH_WORKER_NAME)
        assert lease is not None and lease.status == "RUNNING"
    engine.dispose()


def test_research_job_runner_rejects_invalid_lease_duration():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        calls: list[str] = []
        outcome = run_research_job(session, "research-invalid", 0,
                                   lambda _token: calls.append("wrong"))
        assert outcome.status == "FAILED"
        assert calls == []
        token = acquire_research_lease(session, "research-valid", 60)
        assert token is not None
        assert not renew_research_lease(session, token, 0)
        assert assert_research_lease(session, token)
    engine.dispose()


def test_research_job_runner_cancels_before_task_and_releases_lease():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        calls: list[str] = []
        outcome = run_research_job(
            session, "research-cancel-before-start", 60,
            lambda _token: calls.append("wrong"), cancel_requested=lambda: True,
        )
        assert outcome.status == "CANCELLED"
        assert calls == []
        lease = session.get(WorkerLease, RESEARCH_WORKER_NAME)
        assert lease is not None and lease.status == "CANCELLED"
        replacement = acquire_research_lease(session, "research-after-cancel", 60)
        assert replacement is not None and assert_research_lease(session, replacement)
    engine.dispose()


def test_research_job_runner_records_cooperative_task_cancellation():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        outcome = run_research_job(
            session, "research-cancel-during-task", 60,
            lambda _token: (_ for _ in ()).throw(ResearchJobCancelled()),
        )
        assert outcome.status == "CANCELLED"
        lease = session.get(WorkerLease, RESEARCH_WORKER_NAME)
        assert lease is not None and lease.status == "CANCELLED"
    engine.dispose()


def test_research_lease_acquisition_rejects_invalid_duration():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for seconds in (0, -1):
            try:
                acquire_research_lease(session, "invalid-research", seconds)
            except ValueError as exc:
                assert str(exc) == "Research lease duration must be positive."
            else:
                raise AssertionError("Invalid research lease duration was accepted")
        assert session.get(WorkerLease, RESEARCH_WORKER_NAME) is None
    engine.dispose()


def test_failed_task_rolls_back_before_recording_degraded_lease():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        def fail_during_flush(_token) -> None:
            session.expunge_all()
            now = datetime.now(timezone.utc)
            session.add(WorkerLease(
                name=RESEARCH_WORKER_NAME, owner="duplicate", generation=0,
                lease_until=now, heartbeat_at=now, status="RUNNING", detail="",
            ))
            session.flush()

        outcome = run_research_job(session, "research-failed-flush", 60,
                                   fail_during_flush)
        assert outcome.status == "FAILED"
        lease = session.get(WorkerLease, RESEARCH_WORKER_NAME)
        assert lease is not None and lease.status == "DEGRADED"
        assert acquire_research_lease(session, "research-recovered", 60) is not None
    engine.dispose()


def test_stale_task_rolls_back_its_uncommitted_work(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        checks = iter((True, False))
        monkeypatch.setattr(research_scheduler_lease, "assert_research_lease",
                            lambda _session, _token: next(checks))

        def stage_without_commit(_token) -> None:
            now = datetime.now(timezone.utc)
            session.add(WorkerLease(
                name="uncommitted-research-row", owner="stale", generation=0,
                lease_until=now, heartbeat_at=now, status="RUNNING", detail="",
            ))

        outcome = run_research_job(session, "research-stale", 60,
                                   stage_without_commit)
        assert outcome.status == "STALE"
        assert session.get(WorkerLease, "uncommitted-research-row") is None
    engine.dispose()
