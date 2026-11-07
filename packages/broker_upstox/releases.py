"""The three locks before any live send (LIVE_TRADING.md L5): the config flag, an owner-signed
release record and an owner arm, plus the kill switch. Every lock fails closed.

The signing key is the owner's secret, supplied by the environment of the command that uses
it; it is never stored in the database or in `.env`.
"""

import hashlib
import hmac
import json
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from packages.broker_upstox.halt import live_halted
from packages.database.clock import monotonic_utc_now
from packages.database.models import LiveArmEvent, LiveRelease

MIN_KEY_LENGTH = 32


def _payload(release: LiveRelease) -> bytes:
    return json.dumps({
        "id": str(release.id), "owner": str(release.owner_user_id),
        "strategy": release.strategy_key, "max_lots": release.max_lots,
        "max_trade_loss": str(Decimal(release.max_trade_loss).quantize(Decimal("0.01"))),
        "daily_loss_cap": str(Decimal(release.daily_loss_cap).quantize(Decimal("0.01"))),
        "valid_until": release.valid_until.isoformat(),
    }, sort_keys=True).encode()


def _sign(key: str, release: LiveRelease) -> str:
    if len(key) < MIN_KEY_LENGTH:
        raise ValueError(f"The signing key must be at least {MIN_KEY_LENGTH} characters")
    return hmac.new(key.encode(), _payload(release), hashlib.sha256).hexdigest()


def create_release(session: Session, *, key: str, owner_user_id: UUID, strategy_key: str,
                   max_lots: int, max_trade_loss: Decimal, daily_loss_cap: Decimal,
                   valid_until: date) -> LiveRelease:
    if max_lots < 1 or max_trade_loss <= 0 or daily_loss_cap <= 0:
        raise ValueError("Lots and loss caps must be positive")
    release = LiveRelease(id=uuid4(), owner_user_id=owner_user_id,
                          strategy_key=strategy_key, max_lots=max_lots,
                          max_trade_loss=max_trade_loss, daily_loss_cap=daily_loss_cap,
                          valid_until=valid_until, signature="")
    release.signature = _sign(key, release)
    session.add(release)
    session.commit()
    return release


def revoke_release(session: Session, release_id: UUID) -> None:
    release = session.get(LiveRelease, release_id)
    if release is None:
        raise ValueError("No such release")
    if release.revoked_at is None:
        release.revoked_at = monotonic_utc_now()
    session.commit()


def set_armed(session: Session, release_id: UUID, *, armed: bool, actor: str,
              reason: str) -> None:
    if not reason.strip():
        raise ValueError("A reason is required")
    if session.get(LiveRelease, release_id) is None:
        raise ValueError("No such release")
    session.add(LiveArmEvent(release_id=release_id, armed=armed, actor=actor[:64],
                             reason=reason[:256]))
    session.commit()


def send_blockers(session: Session, *, live_enabled: bool, release_id: UUID, key: str,
                  today: date) -> list[str]:
    """Every lock that is closed. Empty means all four are open; any error closes them all."""
    blockers = [] if live_enabled else ["LIVE_DISABLED"]
    try:
        if live_halted(session):
            blockers.append("LIVE_HALTED")
        release = session.get(LiveRelease, release_id)
        if release is None:
            return [*blockers, "RELEASE_MISSING"]
        if release.revoked_at is not None:
            blockers.append("RELEASE_REVOKED")
        if today > release.valid_until:
            blockers.append("RELEASE_EXPIRED")
        try:
            valid = hmac.compare_digest(release.signature, _sign(key, release))
        except ValueError:
            valid = False
        if not valid:
            blockers.append("RELEASE_SIGNATURE")
        armed = session.scalar(
            select(LiveArmEvent.armed).where(LiveArmEvent.release_id == release_id)
            .order_by(LiveArmEvent.occurred_at.desc()).limit(1)
        )
        if armed is not True:
            blockers.append("NOT_ARMED")
    except SQLAlchemyError:
        session.rollback()
        return [*blockers, "LOCKS_UNREADABLE"]
    return blockers
