import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from packages.market_data.contracts import Quote
from packages.market_data.feed_journal import FeedJournal
from packages.market_data.shared_feed import SharedQuoteFeed


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 0, -1])
@pytest.mark.parametrize("setting", ["max_age_seconds", "reconnect_delay_seconds"])
def test_invalid_timing_cannot_disable_expiry_or_reconnect_bounds(setting, value):
    with pytest.raises(ValueError, match="finite and positive"):
        SharedQuoteFeed(ControlledProvider(), ["NSE_EQ|RELIANCE"], **{setting: value})


class ControlledProvider:
    def __init__(self) -> None:
        self.subscriptions: list[list[str]] = []
        self.streams: list[asyncio.Queue[Quote | Exception | None]] = []

    def subscribe(self, keys: list[str]):  # type: ignore[no-untyped-def]
        async def stream():  # type: ignore[no-untyped-def]
            queue: asyncio.Queue[Quote | Exception | None] = asyncio.Queue()
            self.subscriptions.append(keys)
            self.streams.append(queue)
            while True:
                item = await queue.get()
                if isinstance(item, Exception):
                    raise item
                if item is None:
                    return
                yield item

        return stream()


async def until(predicate):  # type: ignore[no-untyped-def]
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("feed did not reach expected state")


def quote(price: str = "100") -> Quote:
    return Quote(
        instrument_token="NSE_EQ|RELIANCE",
        timestamp=datetime.now(timezone.utc),
        timestamp_basis="provider_response",
        book_timestamp_basis="unknown",
        last_price=Decimal(price),
        bid=Decimal("99"),
        ask=Decimal("101"),
    )


def test_one_connection_serves_multiple_readers_without_upgrading_provenance(tmp_path):
    async def scenario() -> None:
        provider = ControlledProvider()
        journal = FeedJournal(tmp_path / "feed.jsonl")
        feed = SharedQuoteFeed(
            provider, ["NSE_EQ|RELIANCE", "NSE_EQ|RELIANCE"],
            reconnect_delay_seconds=0.02,
            journal=journal,
        )
        try:
            feed.start()
            feed.start()
            await until(lambda: len(provider.streams) == 1)
            provider.streams[0].put_nowait(quote())
            await until(lambda: feed.get("NSE_EQ|RELIANCE") is not None)
            first = feed.get("NSE_EQ|RELIANCE")
            second = feed.get("NSE_EQ|RELIANCE")
            assert first is not None and second is not None
            assert first is not second
            assert second.book_as_of is None
            assert second.book_timestamp_basis == "unknown"
            assert provider.subscriptions == [["NSE_EQ|RELIANCE"]]
            assert feed.status().cached_count == 1
        finally:
            await feed.stop()
            journal.close()
        assert feed.get("NSE_EQ|RELIANCE") is None
        assert not feed.status().running
        records = [json.loads(line) for line in (tmp_path / "feed.jsonl").read_text().splitlines()]
        assert [row["event"] for row in records] == [
            "connected", "quote", "disconnected", "stopped",
        ]

    asyncio.run(scenario())


def test_disconnect_discards_quote_then_reconnects_without_leaking_error():
    async def scenario() -> None:
        provider = ControlledProvider()
        feed = SharedQuoteFeed(
            provider, ["NSE_EQ|RELIANCE"], reconnect_delay_seconds=0.03,
        )
        try:
            feed.start()
            await until(lambda: len(provider.streams) == 1)
            provider.streams[0].put_nowait(quote())
            await until(lambda: feed.get("NSE_EQ|RELIANCE") is not None)
            provider.streams[0].put_nowait(RuntimeError("private-token-and-url"))
            await until(lambda: feed.status().reconnects == 1)
            assert feed.get("NSE_EQ|RELIANCE") is None
            assert not feed.status().connected
            assert "private-token-and-url" not in repr(feed.status())
            await until(lambda: len(provider.streams) == 2)
            provider.streams[1].put_nowait(quote("102"))
            await until(lambda: feed.get("NSE_EQ|RELIANCE") is not None)
            assert feed.get("NSE_EQ|RELIANCE").last_price == Decimal("102")  # type: ignore[union-attr]
        finally:
            await feed.stop()

    asyncio.run(scenario())


def test_local_receive_freshness_expires_even_when_stream_stays_open():
    async def scenario() -> None:
        provider = ControlledProvider()
        feed = SharedQuoteFeed(
            provider, ["NSE_EQ|RELIANCE"], max_age_seconds=0.01,
        )
        try:
            feed.start()
            await until(lambda: len(provider.streams) == 1)
            provider.streams[0].put_nowait(quote())
            await until(lambda: feed.get("NSE_EQ|RELIANCE") is not None)
            await asyncio.sleep(0.02)
            assert feed.get("NSE_EQ|RELIANCE") is None
            assert feed.status().cached_count == 0
        finally:
            await feed.stop()

    asyncio.run(scenario())
