import sqlite3
from collections.abc import Generator
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from apps.api.settings import get_settings

# Local services share one SQLite file. In SQLite's default journal mode one long read blocks
# every write, which made the paper worker lose its cycle while a backtest ran. With a
# write-ahead log readers and writers don't block each other, and a writer waits its turn.
SQLITE_BUSY_SECONDS = 30


def _use_write_ahead_log(connection: Any, _record: Any) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        # Another process is mid-write; the setting persists, so a later connection applies it.
        pass
    finally:
        cursor.close()


@lru_cache(maxsize=8)
def build_session_factory(url: str | None = None) -> sessionmaker[Session]:
    database_url = url or get_settings().database_url
    sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False, "timeout": SQLITE_BUSY_SECONDS} if sqlite else {}
    engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
    if sqlite:
        event.listen(engine, "connect", _use_write_ahead_log)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session = build_session_factory()()
    try:
        yield session
    finally:
        session.close()
