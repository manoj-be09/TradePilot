"""Database leases with fencing: an expired worker cannot commit exit decisions."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from packages.database.models import WorkerLease

WORKER_NAME = "paper-exits"


@dataclass(frozen=True)
class LeaseToken:
    owner: str
    generation: int
    name: str = WORKER_NAME


def acquire(session: Session, owner: str, seconds: int,
            worker_name: str = WORKER_NAME) -> LeaseToken | None:
    now = datetime.now(timezone.utc)
    dialect = session.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("Paper worker supports PostgreSQL and local SQLite only")
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    session.execute(
        insert(WorkerLease)
        .values(
            name=worker_name,
            owner="",
            generation=0,
            lease_until=now,
            heartbeat_at=now,
            status="STOPPED",
            detail="",
        )
        .on_conflict_do_nothing(index_elements=["name"])
    )
    generation = session.execute(
        update(WorkerLease)
        .where(
            WorkerLease.name == worker_name,
            WorkerLease.lease_until <= now,
        )
        .values(
            owner=owner,
            generation=WorkerLease.generation + 1,
            lease_until=now + timedelta(seconds=seconds),
            heartbeat_at=now,
            status="RUNNING",
            detail="Checking paper exits",
        )
        .returning(WorkerLease.generation)
    ).scalar_one_or_none()
    session.commit()
    return LeaseToken(owner, generation, worker_name) if generation is not None else None


def assert_owned(session: Session, token: LeaseToken) -> bool:
    # Keep this lock until the account mutation commits. Acquisition never locks accounts.
    lease = session.scalar(
        select(WorkerLease)
        .where(
            WorkerLease.name == token.name,
            WorkerLease.owner == token.owner,
            WorkerLease.generation == token.generation,
            WorkerLease.lease_until > datetime.now(timezone.utc),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return lease is not None


def renew(session: Session, token: LeaseToken, seconds: int) -> bool:
    """Extend a live lease only for its current fenced owner."""
    now = datetime.now(timezone.utc)
    renewed_generation = session.execute(
        update(WorkerLease).execution_options(synchronize_session=False)
        .where(
            WorkerLease.name == token.name,
            WorkerLease.owner == token.owner,
            WorkerLease.generation == token.generation,
            WorkerLease.lease_until > now,
        )
        .values(
            lease_until=now + timedelta(seconds=seconds),
            heartbeat_at=now,
        )
        .returning(WorkerLease.generation)
    ).scalar_one_or_none()
    session.commit()
    return renewed_generation is not None


def finish(session: Session, token: LeaseToken, status: str, detail: str) -> None:
    now = datetime.now(timezone.utc)
    values: dict[str, object] = dict(
        heartbeat_at=now,
        lease_until=now,
        status=status,
        detail=detail[:512],
    )
    if status == "HEALTHY":
        values["last_success_at"] = now
    session.execute(
        update(WorkerLease).execution_options(synchronize_session=False)
        .where(
            WorkerLease.name == token.name,
            WorkerLease.owner == token.owner,
            WorkerLease.generation == token.generation,
            WorkerLease.lease_until > now,
        )
        .values(**values)
    )
    session.commit()
