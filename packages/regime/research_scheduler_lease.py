"""Bounded research-job lease boundary; no trading or auto-promotion is enabled."""

from dataclasses import dataclass
from typing import Callable, Literal

from sqlalchemy.orm import Session

from packages.paper_trading.worker_lease import LeaseToken, acquire, assert_owned, finish, renew

RESEARCH_WORKER_NAME = "research-evaluator"


@dataclass(frozen=True)
class ResearchJobOutcome:
    status: Literal["COMPLETED", "SKIPPED", "FAILED", "STALE"]
    detail: str


def acquire_research_lease(session: Session, owner: str, seconds: int) -> LeaseToken | None:
    """Acquire a fenced research lease using the existing lease table."""
    return acquire(session, owner, seconds, worker_name=RESEARCH_WORKER_NAME)


def assert_research_lease(session: Session, token: LeaseToken) -> bool:
    """Reject stale research workers before a future scheduler mutation."""
    return token.name == RESEARCH_WORKER_NAME and assert_owned(session, token)


def renew_research_lease(session: Session, token: LeaseToken, seconds: int) -> bool:
    """Keep a live research reservation fenced to its current owner."""
    return token.name == RESEARCH_WORKER_NAME and renew(session, token, seconds)


def finish_research_lease(session: Session, token: LeaseToken, status: str, detail: str) -> None:
    """Release only the matching generation; stale owners cannot overwrite status."""
    if token.name == RESEARCH_WORKER_NAME:
        finish(session, token, status, detail)


def run_research_job(session: Session, owner: str, seconds: int,
                     task: Callable[[], None]) -> ResearchJobOutcome:
    """Run one bounded research task under a fenced lease.

    The task is deliberately injected and receives no trading session or order
    capability. This helper only owns lease acquisition, takeover detection and
    bounded status updates; PostgreSQL concurrency and crash recovery remain
    deployment acceptance gates.
    """
    if seconds <= 0:
        return ResearchJobOutcome("FAILED", "Research lease duration must be positive.")
    token = acquire_research_lease(session, owner, seconds)
    if token is None:
        return ResearchJobOutcome("SKIPPED", "Another research worker owns the lease.")
    if not assert_research_lease(session, token):
        return ResearchJobOutcome("STALE", "Research lease was lost before task start.")
    try:
        task()
    except Exception:
        finish_research_lease(session, token, "DEGRADED", "Research task failed.")
        return ResearchJobOutcome("FAILED", "Research task failed; no exception details retained.")
    if not assert_research_lease(session, token):
        return ResearchJobOutcome("STALE", "Research lease was taken over before completion.")
    finish_research_lease(session, token, "HEALTHY", "Research task completed.")
    return ResearchJobOutcome("COMPLETED", "Research task completed under a fenced lease.")
