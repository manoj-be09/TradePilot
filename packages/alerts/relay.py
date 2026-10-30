"""Collect paper-trading alerts for a phone: new account notifications and exit-worker trouble.

Read-only over the database. Only one named account's notifications are collected, so a
shared local install never forwards another user's trades. The shared exit-worker state
is reported on change: once when it turns bad (stale, stopped, error, warnings) and once
when it recovers. Messages are plain text and carry no credentials or account ids.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.database.models import User, UserNotification, WorkerLease
from packages.paper_trading.worker_lease import WORKER_NAME

PREFIX = "TradePilot (paper)"
_BAD = {"STALE", "NOT_STARTED", "STOPPED", "ERROR", "DEGRADED", "DISABLED"}
_WORKER_TEXT = {
    "STALE": "Exit worker has stopped updating. Automatic exits and entries are not running.",
    "NOT_STARTED": "Exit worker has not started. Automatic exits and entries are not running.",
    "STOPPED": "Exit worker is stopped. Automatic exits and entries are not running.",
    "ERROR": "Exit worker cycle failed. Check the worker before relying on automatic exits.",
    "DEGRADED": "Exit worker reported warnings. Review your positions' latest checks.",
    "DISABLED": "Paper trading is disabled by configuration.",
}


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def worker_state(session: Session, now: datetime, interval_seconds: int) -> str:
    """Same rule as the dashboard: no heartbeat for 3 intervals + 10 s means STALE."""
    lease = session.get(WorkerLease, WORKER_NAME)
    if lease is None:
        return "NOT_STARTED"
    age = (now - _aware(lease.heartbeat_at)).total_seconds()
    return "STALE" if age > interval_seconds * 3 + 10 else lease.status


@dataclass
class AlertCollector:
    """Remembers what it has already reported; start it at the moment the relay starts."""

    user_email: str
    since: datetime
    interval_seconds: int
    worker_reported: str | None = None
    _seen: set[UUID] = field(default_factory=set)

    def collect(self, session: Session, now: datetime) -> list[str]:
        messages: list[str] = []
        email = self.user_email.strip().lower()
        user_id = session.scalar(select(User.id).where(User.email == email))
        if user_id is not None:
            # Compared in Python: SQLite stores these times without an offset.
            recent = list(session.scalars(
                select(UserNotification).where(UserNotification.user_id == user_id)
                .order_by(UserNotification.created_at.desc()).limit(50)
            ))
            for row in sorted(recent, key=lambda item: (_aware(item.created_at), str(item.id))):
                if row.id in self._seen or _aware(row.created_at) < self.since:
                    continue
                self._seen.add(row.id)
                messages.append(f"{PREFIX}: {row.title}\n{row.message}")
        state = worker_state(session, now, self.interval_seconds)
        bad = state in _BAD
        was_bad = self.worker_reported in _BAD
        if self.worker_reported is None:
            if bad:
                messages.append(f"{PREFIX}: {_WORKER_TEXT[state]}")
        elif bad and state != self.worker_reported:
            messages.append(f"{PREFIX}: {_WORKER_TEXT[state]}")
        elif not bad and was_bad:
            messages.append(f"{PREFIX}: Exit worker is running normally again.")
        self.worker_reported = state
        return messages
