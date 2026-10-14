import json
from datetime import datetime, timezone

import pytest

from packages.market_data.contracts import Quote
from packages.market_data.feed_journal import FeedJournal


def test_journal_survives_reopening_omits_private_fields_and_bounds_files(tmp_path):
    path = tmp_path / "feed.jsonl"
    journal = FeedJournal(path)
    quote = Quote(
        instrument_token="private-token-and-url", timestamp=datetime.now(timezone.utc),
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
    reopened = FeedJournal(path)
    reopened.record("disconnected")
    assert len(path.read_text().splitlines()) == 3
    with pytest.raises(ValueError, match="Unsupported feed event"):
        reopened.record("private-exception")
    # Small fixture boundary exercises the same standard-library rotation policy.
    reopened._handler.maxBytes = 400
    for _ in range(20):
        reopened.record("quote", quote)
    reopened.close()
    assert len(list(tmp_path.glob("feed.jsonl*"))) <= 3
    assert all(p.stat().st_size < 400 for p in tmp_path.glob("feed.jsonl*"))
