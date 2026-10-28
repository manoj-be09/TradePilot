"""Idempotent writes of recorder output; a replayed minute never duplicates a row."""

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from packages.database.recorder_models import (
    MarketSnapshot1m,
    RecordedInstrument,
    RecorderDaySummary,
    RecorderEvent,
)
from packages.recorder.universe import RecordedSpec


def _insert_ignoring_duplicates(
    session: Session, model: Any, rows: Sequence[dict[str, Any]]
) -> None:
    if not rows:
        return
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        session.execute(postgresql.insert(model).values(list(rows)).on_conflict_do_nothing())
    elif dialect == "sqlite":
        session.execute(sqlite.insert(model).values(list(rows)).on_conflict_do_nothing())
    else:
        session.execute(insert(model), list(rows))


class RecorderStorage:
    def __init__(self, factory: sessionmaker[Session], trading_day: date) -> None:
        self._factory = factory
        self.trading_day = trading_day

    def write_snapshots(self, rows: Sequence[dict[str, Any]], *, batch: int = 500) -> None:
        with self._factory() as session:
            for start in range(0, len(rows), batch):
                _insert_ignoring_duplicates(session, MarketSnapshot1m, rows[start:start + batch])
            session.commit()

    def write_instruments(self, specs: Iterable[RecordedSpec], at: datetime) -> None:
        rows = [
            {
                "trading_day": self.trading_day, "instrument_key": spec.instrument_key,
                "kind": spec.kind, "trading_symbol": spec.trading_symbol,
                "expiry": spec.expiry, "strike": spec.strike, "option_type": spec.option_type,
                "lot_size": spec.lot_size, "tick_size": spec.tick_size,
                "first_subscribed_at": at,
            }
            for spec in specs
        ]
        with self._factory() as session:
            _insert_ignoring_duplicates(session, RecordedInstrument, rows)
            session.commit()

    def event(self, kind: str, **detail: Any) -> None:
        with self._factory() as session:
            session.add(RecorderEvent(
                trading_day=self.trading_day, occurred_at=datetime.now(timezone.utc),
                kind=kind, detail=detail,
            ))
            session.commit()

    def write_summary(
        self, *, session_start: datetime, session_end: datetime, index_key: str,
        frames: int, raw_bytes: int,
    ) -> RecorderDaySummary:
        """Coverage comes from stored rows and events, so it survives recorder restarts.

        `frames` is counted by the current process only; `raw_bytes` is measured on disk.
        """
        in_session = (
            MarketSnapshot1m.trading_day == self.trading_day,
            MarketSnapshot1m.bucket_start >= session_start,
            MarketSnapshot1m.bucket_start < session_end,
        )
        with self._factory() as session:
            snapshot_rows = session.scalar(
                select(func.count()).select_from(MarketSnapshot1m).where(
                    MarketSnapshot1m.trading_day == self.trading_day
                )
            ) or 0
            connected_minutes = session.scalar(
                select(func.count(func.distinct(MarketSnapshot1m.bucket_start))).where(*in_session)
            ) or 0
            index_minutes = session.scalar(
                select(func.count()).select_from(MarketSnapshot1m).where(
                    *in_session, MarketSnapshot1m.instrument_key == index_key,
                )
            ) or 0
            instruments = session.scalar(
                select(func.count()).select_from(RecordedInstrument).where(
                    RecordedInstrument.trading_day == self.trading_day
                )
            ) or 0
            disconnects = session.scalar(
                select(func.count()).select_from(RecorderEvent).where(
                    RecorderEvent.trading_day == self.trading_day,
                    RecorderEvent.kind == "DISCONNECTED",
                )
            ) or 0
            summary = session.get(RecorderDaySummary, self.trading_day) or RecorderDaySummary(
                trading_day=self.trading_day
            )
            summary.session_start = session_start
            summary.session_end = session_end
            summary.instruments = instruments
            summary.session_minutes = int((session_end - session_start).total_seconds() // 60)
            summary.connected_minutes = connected_minutes
            summary.index_minutes = index_minutes
            summary.snapshot_rows = snapshot_rows
            summary.frames = frames
            summary.raw_bytes = raw_bytes
            summary.disconnects = disconnects
            summary.updated_at = datetime.now(timezone.utc)
            session.add(summary)
            session.commit()
            return summary
