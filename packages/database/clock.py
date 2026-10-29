"""Strictly increasing UTC timestamps for rows whose order is replayed.

The system clock can return the same value for many consecutive calls (coarse on
Windows), and audit observations that tie are otherwise ordered by random UUID. Within
one process each call is at least one microsecond after the previous one.
"""

import threading
from datetime import datetime, timedelta, timezone

_lock = threading.Lock()
_last = datetime.min.replace(tzinfo=timezone.utc)


def monotonic_utc_now() -> datetime:
    global _last
    with _lock:
        now = datetime.now(timezone.utc)
        if now <= _last:
            now = _last + timedelta(microseconds=1)
        _last = now
        return now
