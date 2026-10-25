"""Bounded local feed diagnostics; never accept connection strings or exceptions."""

import base64
import hashlib
import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

from packages.market_data.contracts import Quote

MAX_RAW_FRAME_BYTES = 100_000

FeedEvent = Literal["connected", "disconnected", "reconnect", "stale", "quote", "stopped"]


class FeedJournal:
    """Three files of at most 512 KiB; samples are observations, never fill evidence."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2, encoding="utf-8")
        self._handler.setFormatter(logging.Formatter("%(message)s"))

    def record(self, event: FeedEvent, quote: Quote | None = None) -> None:
        if event not in ("connected", "disconnected", "reconnect", "stale", "quote", "stopped"):
            raise ValueError("Unsupported feed event")
        data: dict[str, object] = {
            "event": event, "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        if quote is not None:
            # Deliberately omit raw payloads, instrument keys, URLs and exception text.
            data.update(
                instrument_digest=hashlib.sha256(quote.instrument_token.encode()).hexdigest(),
                quote_at=quote.timestamp.isoformat(), timestamp_basis=quote.timestamp_basis,
                received_at=quote.received_at.isoformat() if quote.received_at else None,
                trade_at=quote.last_trade_time.isoformat() if quote.last_trade_time else None,
                book_at=quote.book_as_of.isoformat() if quote.book_as_of else None,
                book_basis=quote.book_timestamp_basis, last=str(quote.last_price),
                bid=str(quote.bid) if quote.bid is not None else None,
                ask=str(quote.ask) if quote.ask is not None else None,
                bid_size=quote.bid_quantity, ask_size=quote.ask_quantity,
            )
        self._handler.emit(logging.LogRecord(
            "feed_journal", logging.INFO, "", 0, json.dumps(data), (), None,
        ))

    def record_frame(self, payload: bytes, received_at: datetime) -> None:
        """Retain exact bounded V3 frame bytes without connection metadata."""
        if received_at.tzinfo is None:
            raise ValueError("Frame receipt time must have a timezone")
        data: dict[str, object] = {
            "event": "raw_v3_frame",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "received_at": received_at.isoformat(),
            "byte_count": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        if len(payload) > MAX_RAW_FRAME_BYTES:
            data["event"] = "raw_v3_frame_omitted"
            data["reason"] = "frame_size_limit"
        else:
            data["payload_base64"] = base64.b64encode(payload).decode("ascii")
        self._handler.emit(logging.LogRecord(
            "feed_journal", logging.INFO, "", 0, json.dumps(data), (), None,
        ))

    def close(self) -> None:
        self._handler.close()
