import asyncio
import json
from datetime import datetime, timezone

import pytest

from apps.feed_observer.main import observe
from packages.market_data.contracts import Quote
from packages.market_data.feed_journal import FeedJournal


@pytest.mark.parametrize("status", ["NORMAL_OPEN", "NORMAL_CLOSE", "ERROR"])
def test_observer_only_subscribes_when_normal_open_and_persists_safe_events(tmp_path, status):
    class Provider:
        subscriptions = 0

        async def get_market_status(self, exchange):
            if status == "ERROR":
                raise RuntimeError("private-token-url")
            return status

        async def subscribe(self, instruments):
            self.subscriptions += 1
            yield Quote(
                instrument_token=instruments[0], timestamp=datetime.now(timezone.utc),
                timestamp_basis="provider_tick", last_price=100,
            )
            await asyncio.Event().wait()

    provider = Provider()
    path = tmp_path / "observer.jsonl"
    journal = FeedJournal(path)
    try:
        asyncio.run(observe(provider, ["test"], journal, once=True, interval=0.02))
    finally:
        journal.close()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    events = [row["event"] for row in rows]
    assert provider.subscriptions == (1 if status == "NORMAL_OPEN" else 0)
    assert ("quote" in events) == (status == "NORMAL_OPEN")
    assert events[-1] == "stopped"
    assert "private-token-url" not in path.read_text()


def test_observer_logs_status_failures_without_exception_text(tmp_path, caplog):
    class Provider:
        async def get_market_status(self, exchange):
            raise RuntimeError("private-token-url")

    journal = FeedJournal(tmp_path / "observer.jsonl")
    try:
        with caplog.at_level("WARNING", logger="feed_observer"):
            asyncio.run(observe(Provider(), ["test"], journal, once=True, interval=0.01))
    finally:
        journal.close()
    assert "RuntimeError" in caplog.text
    assert "private-token-url" not in caplog.text
