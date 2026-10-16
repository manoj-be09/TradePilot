from datetime import timedelta

from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from packages.database.base import Base
from packages.database.models import WorkerLease
from packages.regime.research_scheduler_lease import (
    RESEARCH_WORKER_NAME,
    acquire_research_lease,
    assert_research_lease,
    finish_research_lease,
)


def test_research_lease_fences_takeover_and_stale_finish():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        old = acquire_research_lease(session, "old-research", 60)
        assert old is not None and assert_research_lease(session, old)
        current = session.get(WorkerLease, RESEARCH_WORKER_NAME)
        assert current is not None
        session.execute(update(WorkerLease).where(
            WorkerLease.name == RESEARCH_WORKER_NAME,
        ).values(lease_until=current.lease_until - timedelta(seconds=120)))
        session.commit()
        new = acquire_research_lease(session, "new-research", 60)
        assert new is not None and new.generation > old.generation
        assert not assert_research_lease(session, old)
        assert assert_research_lease(session, new)
        finish_research_lease(session, old, "ERROR", "stale result")
        current = session.get(WorkerLease, RESEARCH_WORKER_NAME)
        assert current is not None and current.owner == "new-research"
        finish_research_lease(session, new, "HEALTHY", "review cycle complete")
        assert not assert_research_lease(session, new)
    engine.dispose()
