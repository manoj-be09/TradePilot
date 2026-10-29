"""Lossless, append-only storage of exact Feed V3 frame bytes.

Layout: <root>/<YYYY-MM-DD>/<HH>.frames.gz (UTC hour). Each record is
`<q I` (receipt time in epoch nanoseconds, payload length) followed by the payload.
Files are gzip streams sync-flushed periodically, so a crash loses at most the last
few seconds and readers stop cleanly at a truncated tail. Keep this directory private:
Upstox permits internal retention, not redistribution.
"""

import gzip
import struct
import zlib
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

_HEADER = struct.Struct("<qI")
MAX_FRAME_BYTES = 16_000_000


class RawFrameStore:
    def __init__(self, root: Path, *, flush_interval_seconds: float = 5.0) -> None:
        self._root = root
        self._flush_interval = flush_interval_seconds
        self._file: gzip.GzipFile | None = None
        self._path: Path | None = None
        self._last_flush = monotonic()
        self.frames = 0
        self.bytes = 0

    def append(self, payload: bytes, received_at: datetime) -> None:
        if received_at.tzinfo is None:
            raise ValueError("Frame receipt time must have a timezone")
        if len(payload) > MAX_FRAME_BYTES:
            raise ValueError("Frame exceeds the raw store size limit")
        stamp = received_at.astimezone(timezone.utc)
        handle = self._handle_for(stamp)
        nanos = int(stamp.timestamp()) * 1_000_000_000 + stamp.microsecond * 1_000
        handle.write(_HEADER.pack(nanos, len(payload)))
        handle.write(payload)
        self.frames += 1
        self.bytes += len(payload)
        if monotonic() - self._last_flush >= self._flush_interval:
            self.flush()

    def flush(self) -> None:
        if self._file is not None:
            self._file.flush(zlib.Z_SYNC_FLUSH)
        self._last_flush = monotonic()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
        self._file = None
        self._path = None

    def _handle_for(self, stamp: datetime) -> gzip.GzipFile:
        path = self._root / stamp.strftime("%Y-%m-%d") / f"{stamp:%H}.frames.gz"
        if self._file is None or path != self._path:
            self.close()
            path.parent.mkdir(parents=True, exist_ok=True)
            # Appending starts a new gzip member; concatenated members read as one stream.
            self._file = gzip.GzipFile(path, mode="ab")
            self._path = path
        return self._file


def iter_frames(path: Path) -> Iterator[tuple[datetime, bytes]]:
    """Yield (receipt time, payload) records; stop quietly at a truncated tail."""
    with path.open("rb") as raw:
        stream = gzip.GzipFile(fileobj=raw, mode="rb")
        while True:
            try:
                header = stream.read(_HEADER.size)
                if len(header) < _HEADER.size:
                    return
                nanos, length = _HEADER.unpack(header)
                payload = stream.read(length)
            except (EOFError, gzip.BadGzipFile, zlib.error):
                return
            if len(payload) < length:
                return
            yield datetime.fromtimestamp(nanos / 1_000_000_000, timezone.utc), payload
