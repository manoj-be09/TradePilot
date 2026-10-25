import json
from datetime import datetime, timezone

import pytest

from packages.market_data.contracts import Quote
from packages.market_data.feed_journal import FeedJournal


def test_journal_survives_reopening_omits_private_fields_and_bounds_files(tmp_path):
    path = tmp_path / "feed.jsonl"
    journal = FeedJournal(path)
    received_at = datetime.now(timezone.utc)
    quote = Quote(
        instrument_token="private-token-and-url", timestamp=received_at,
        received_at=received_at,
        last_price=100, timestamp_basis="provider_tick",
    )
    journal.record("connected")
    journal.record("quote", quote)
    journal.close()
    data = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["event"] for row in data] == ["connected", "quote"]
    assert "private-token-and-url" not in path.read_text()
    assert data[-1]["book_basis"] == "unknown"
    assert data[-1]["book_at"] is None
    assert data[-1]["received_at"] == received_at.isoformat()
    first_instrument = data[-1]["instrument_digest"]
    assert len(first_instrument) == 64
    reopened = FeedJournal(path)
    reopened.record("disconnected")
    assert len(path.read_text().splitlines()) == 3
    reopened.record("quote", quote.model_copy(update={"instrument_token": "different-instrument"}))
    assert json.loads(path.read_text().splitlines()[-1])["instrument_digest"] != first_instrument
    with pytest.raises(ValueError, match="Unsupported feed event"):
        reopened.record("private-exception")
    # Small fixture boundary exercises the same standard-library rotation policy.
    reopened._handler.maxBytes = 800
    for _ in range(20):
        reopened.record("quote", quote)
    reopened.close()
    assert len(list(tmp_path.glob("feed.jsonl*"))) <= 3
    assert all(p.stat().st_size < 800 for p in tmp_path.glob("feed.jsonl*"))



def test_exact_v3_frame_journal_round_trip_and_explicit_oversize(tmp_path):
    import base64
    import hashlib

    from packages.market_data.feed_journal import MAX_RAW_FRAME_BYTES

    path = tmp_path / "v3.jsonl"
    journal = FeedJournal(path)
    received_at = datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc)
    payload = b"\x00\xff\x01NSE_FO|TEST"
    journal.record_frame(payload, received_at)
    journal.record_frame(b"x" * (MAX_RAW_FRAME_BYTES + 1), received_at)
    journal.close()

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["event"] == "raw_v3_frame"
    assert rows[0]["received_at"] == received_at.isoformat()
    assert rows[0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert base64.b64decode(rows[0]["payload_base64"]) == payload
    assert rows[1]["event"] == "raw_v3_frame_omitted"
    assert rows[1]["reason"] == "frame_size_limit"
    assert "payload_base64" not in rows[1]
    assert path.stat().st_size < 512_000


def test_v3_frame_journal_requires_aware_receipt_time(tmp_path):
    journal = FeedJournal(tmp_path / "v3.jsonl")
    try:
        with pytest.raises(ValueError, match="timezone"):
            journal.record_frame(b"frame", datetime(2026, 10, 26))
    finally:
        journal.close()
