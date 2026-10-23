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
