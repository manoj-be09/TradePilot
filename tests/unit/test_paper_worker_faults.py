"""Fault injection for one paper worker cycle: a crash or a hang must end the cycle
as ERROR, release the lease for the next cycle, and never store the exception text."""

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import apps.paper_worker.main as worker
from apps.api.settings import Settings
from packages.database import futures_models  # noqa: F401
from packages.database.base import Base
from packages.database.models import WorkerLease
from packages.paper_trading.worker_lease import WORKER_NAME


def _factory():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(engine)


def _settings(lease_seconds: int = 120) -> Settings:
    # Below the validated minimum so a hang times out within a second in tests.
    return Settings(_env_file=None).model_copy(  # type: ignore[call-arg]
        update={"paper_worker_lease_seconds": lease_seconds})


def _lease(factory) -> WorkerLease:
    with factory() as session:
        lease = session.get(WorkerLease, WORKER_NAME)
        assert lease is not None
        session.expunge(lease)
        return lease


def test_a_crashing_cycle_is_recorded_as_error_without_the_exception_text(monkeypatch):
    async def crash(*_args, **_kwargs):
        raise RuntimeError("provider said token=abc123 for user someone@example.com")

    monkeypatch.setattr(worker, "process_option_deployments", crash)
    factory = _factory()
    assert asyncio.run(worker.run_cycle(factory, object(), _settings(), "worker-a")) == "ERROR"
    lease = _lease(factory)
    assert lease.status == "ERROR"
    assert "RuntimeError" in lease.detail
    assert "abc123" not in lease.detail and "example.com" not in lease.detail
    # The lease was released, so the next cycle (any owner) can run straight away.
    monkeypatch.setattr(worker, "process_option_deployments",
                        lambda *_args, **_kwargs: asyncio.sleep(0, result=(0, 0, [])))
    assert asyncio.run(worker.run_cycle(factory, object(), _settings(), "worker-b")) == "HEALTHY"


def test_a_hung_cycle_times_out_inside_its_lease(monkeypatch):
    async def hang(*_args, **_kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(worker, "process_option_deployments", hang)
    factory = _factory()
    assert asyncio.run(worker.run_cycle(factory, object(), _settings(6), "worker-a")) == "ERROR"
    assert "TimeoutError" in _lease(factory).detail


def test_a_second_worker_is_busy_while_the_first_holds_the_lease(monkeypatch):
    factory = _factory()
    results: list[str] = []

    async def nested(*_args, **_kwargs):
        results.append(await worker.run_cycle(factory, object(), _settings(), "worker-b"))
        return 0, 0, []

    monkeypatch.setattr(worker, "process_option_deployments", nested)
    assert asyncio.run(worker.run_cycle(factory, object(), _settings(), "worker-a")) == "HEALTHY"
    assert results == ["BUSY"]
