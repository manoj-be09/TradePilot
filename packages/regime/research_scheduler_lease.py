"""Research scheduler lease boundary; no scheduling or evaluation is enabled."""

from sqlalchemy.orm import Session

from packages.paper_trading.worker_lease import LeaseToken, acquire, assert_owned, finish

RESEARCH_WORKER_NAME = "research-evaluator"


def acquire_research_lease(session: Session, owner: str, seconds: int) -> LeaseToken | None:
    """Acquire a fenced research lease using the existing lease table."""
    return acquire(session, owner, seconds, worker_name=RESEARCH_WORKER_NAME)


def assert_research_lease(session: Session, token: LeaseToken) -> bool:
    """Reject stale research workers before a future scheduler mutation."""
    return token.name == RESEARCH_WORKER_NAME and assert_owned(session, token)


def finish_research_lease(session: Session, token: LeaseToken, status: str, detail: str) -> None:
    """Release only the matching generation; stale owners cannot overwrite status."""
    if token.name == RESEARCH_WORKER_NAME:
        finish(session, token, status, detail)
