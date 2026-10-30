"""Local services share one SQLite file: a long read must not stop the paper worker writing."""

import sqlite3
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import text

from packages.database.session import SQLITE_BUSY_SECONDS, build_session_factory
from packages.database.sqlite_backup import backup_database


def _database(tmp_path, rows: int = 0):
    path = tmp_path / "paper.db"
    factory = build_session_factory(f"sqlite:///{path.as_posix()}")
    with factory() as session:
        session.execute(text("create table t (x blob)"))
        session.commit()
    if rows:
        connection = sqlite3.connect(path)
        connection.executemany("insert into t values (?)", [(b"x" * 4000,)] * rows)
        connection.commit()
        connection.close()
    return path, factory


def test_the_database_uses_a_write_ahead_log_and_waits_for_a_busy_writer(tmp_path):
    _, factory = _database(tmp_path)
    with factory() as session:
        assert session.execute(text("pragma journal_mode")).scalar() == "wal"
        assert session.execute(text("pragma busy_timeout")).scalar() == SQLITE_BUSY_SECONDS * 1000


def test_a_write_succeeds_during_an_open_read(tmp_path):
    path, factory = _database(tmp_path, rows=10)
    reader = sqlite3.connect(path)
    rows = reader.execute("select x from t")
    assert rows.fetchone() is not None  # An open cursor holds a read snapshot.
    try:
        with factory() as session:
            session.execute(text("insert into t values (x'00')"))
            session.commit()
    finally:
        reader.close()


def test_writers_keep_working_during_a_backup(tmp_path):
    path, _ = _database(tmp_path, rows=20000)
    errors: list[Exception] = []
    finished = threading.Event()
    writes = 0

    def write() -> None:
        nonlocal writes
        writer = sqlite3.connect(path, timeout=1)
        try:
            while not finished.is_set():
                writer.execute("insert into t values (x'00')")
                writer.commit()
                writes += 1
                time.sleep(0.01)
        except sqlite3.OperationalError as error:
            errors.append(error)
        finally:
            writer.close()

    thread = threading.Thread(target=write)
    thread.start()
    try:
        moment = datetime(2026, 11, 3, tzinfo=timezone.utc)
        copy = backup_database(path, tmp_path / "backups", moment)
    finally:
        finished.set()
        thread.join()
    assert errors == []
    assert writes > 0
    connection = sqlite3.connect(copy)
    assert connection.execute("select count(*) from t").fetchone()[0] >= 20000
    connection.close()
