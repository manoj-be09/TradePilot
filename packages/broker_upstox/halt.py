"""Live kill switch. Fails closed: no row, a missing table or any read error means halted."""

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from packages.database.models import LiveHaltEvent


def live_halted(session: Session) -> bool:
    try:
        latest = session.scalar(
            select(LiveHaltEvent.halted).order_by(LiveHaltEvent.occurred_at.desc()).limit(1)
        )
    except SQLAlchemyError:
        session.rollback()
        return True
    return latest is not False


def set_live_halt(session: Session, *, halted: bool, actor: str, reason: str) -> None:
    if not reason.strip():
        raise ValueError("A reason is required")
    session.add(LiveHaltEvent(halted=halted, actor=actor[:64], reason=reason[:256]))
    session.commit()
