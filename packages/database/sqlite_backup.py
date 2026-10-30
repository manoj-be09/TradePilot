"""Verified backups of the local SQLite database.

The copy reads one consistent snapshot. In write-ahead-log mode (see session.py) other
processes keep writing while it runs; it still runs after market hours to keep the load off
the machine during the session.
"""

import sqlite3
from datetime import datetime
from pathlib import Path


class BackupError(RuntimeError):
    pass


def _remove_partial_files(directory: Path) -> None:
    # A copy interrupted part way leaves a file nearly as big as the database, and SQLite adds
    # -wal and -shm side files beside a write-ahead-log copy.
    for pattern in ("*.partial*", "*.db-wal", "*.db-shm"):
        for leftover in directory.glob(pattern):
            leftover.unlink(missing_ok=True)


def backup_database(source: Path, directory: Path, moment: datetime, keep: int = 3) -> Path:
    """Copy, prove the copy opens and passes a check, and only then drop the oldest copies."""
    if not source.is_file():
        raise BackupError("The database file does not exist")
    directory.mkdir(parents=True, exist_ok=True)
    _remove_partial_files(directory)
    target = directory / f"{source.stem}-{moment:%Y%m%d-%H%M%S}.db"
    partial = target.with_suffix(".partial")
    reader = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        writer = sqlite3.connect(partial)
        try:
            reader.backup(writer)
        finally:
            writer.close()
    finally:
        reader.close()
    check = sqlite3.connect(f"file:{partial.as_posix()}?mode=ro", uri=True)
    try:
        status = check.execute("PRAGMA quick_check").fetchone()
    finally:
        check.close()
    if status is None or status[0] != "ok":
        partial.unlink(missing_ok=True)
        raise BackupError("The backup copy failed its integrity check")
    # Fold the copy's log in and return it to one self-contained file.
    collapse = sqlite3.connect(partial)
    try:
        collapse.execute("PRAGMA journal_mode=DELETE")
    finally:
        collapse.close()
    partial.replace(target)
    _remove_partial_files(directory)
    copies = sorted(directory.glob(f"{source.stem}-*.db"))
    for old in copies[:-keep]:
        old.unlink()
    return target
