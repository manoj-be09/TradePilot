"""Live trading status and the dashboard halt button. Halting only: releasing stays on the CLI."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from apps.api.auth import CurrentUser, SessionDep
from apps.api.settings import get_settings
from packages.broker_upstox.halt import live_halted, set_live_halt
from packages.database.models import AuditEvent, LiveRelease

router = APIRouter(prefix="/live", tags=["live trading"])


class LiveStatusResponse(BaseModel):
    live_enabled: bool
    halted: bool
    active_releases: int


class LiveHaltRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=256)


def _status(session: SessionDep) -> LiveStatusResponse:
    try:
        releases = session.scalar(select(func.count()).select_from(LiveRelease)
                                  .where(LiveRelease.revoked_at.is_(None))) or 0
    except SQLAlchemyError:
        session.rollback()
        releases = 0
    return LiveStatusResponse(live_enabled=get_settings().effective_live_trading_enabled,
                              halted=live_halted(session), active_releases=releases)


@router.get("/status", response_model=LiveStatusResponse)
def live_status(user: CurrentUser, session: SessionDep) -> LiveStatusResponse:
    return _status(session)


@router.post("/halt", response_model=LiveStatusResponse)
def halt_live(body: LiveHaltRequest, user: CurrentUser, session: SessionDep) -> LiveStatusResponse:
    try:
        set_live_halt(session, halted=True, actor=user.email, reason=body.reason)
        session.add(AuditEvent(actor_user_id=user.id, event_type="live.halted",
                               details={"reason": body.reason}))
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Could not record the halt") from error
    return _status(session)
