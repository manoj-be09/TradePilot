"""Verified backups of the local SQLite database.

The copy holds a read lock for as long as it takes (about 30 seconds at 900 MB), and writers
fail with "database is locked" during that time, so run it after market hours. Copying in
small steps doesn't help: SQLite restarts the copy whenever another process writes.
"""

import sqlite3
from datetime import datetime
from pathlib import Path


class BackupError(RuntimeError):
    pass


def backup_database(source: Path, directory: Path, moment: datetime, keep: int = 7) -> Path:
    """Copy, prove the copy opens and passes a check, and only then drop the oldest copies."""
    if not source.is_file():
        raise BackupError("The database file does not exist")
    directory.mkdir(parents=True, exist_ok=True)
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
    partial.replace(target)
    copies = sorted(directory.glob(f"{source.stem}-*.db"))
    for old in copies[:-keep]:
        old.unlink()
    return target
