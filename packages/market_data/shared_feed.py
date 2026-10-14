"""One read-only provider subscription with a freshness-bounded process-local cache.

This is an observation cache, not executable-book evidence. In particular, it
does not change the timestamp provenance carried by a provider Quote.
"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from time import monotonic

from packages.market_data.contracts import MarketDataProvider, Quote
from packages.market_data.feed_journal import FeedJournal


@dataclass(frozen=True)
class SharedFeedStatus:
    running: bool
    connected: bool
    instrument_count: int
    cached_count: int
    reconnects: int
    last_received_at: datetime | None


class SharedQuoteFeed:
    """Own one subscription; let many in-process readers inspect fresh quotes.

    Create, start, read and stop this object on the same event loop. A separate
    API or worker process needs its own feed or an external shared bus; this
    class makes no cross-process connection-sharing claim.
    """

    def __init__(
        self,
        provider: MarketDataProvider,
        instrument_tokens: list[str],
        *,
        max_age_seconds: float = 10.0,
        reconnect_delay_seconds: float = 2.0,
        journal: FeedJournal | None = None,
    ) -> None:
        keys = list(dict.fromkeys(instrument_tokens))
        if not keys or len(keys) > 100 or any(not key.strip() for key in keys):
            raise ValueError("A shared feed needs 1–100 nonempty instrument keys")
        if any(not isfinite(value) or value <= 0 for value in (
            max_age_seconds, reconnect_delay_seconds,
        )):
            raise ValueError("Feed freshness and reconnect delay must be finite and positive")
        self._provider = provider
        self._journal = journal
        self._keys = keys
        self._max_age = max_age_seconds
        self._reconnect_delay = reconnect_delay_seconds
        self._task: asyncio.Task[None] | None = None
        self._quotes: dict[str, tuple[Quote, float]] = {}
        self._connected = False
        self._reconnects = 0
        self._last_received_at: datetime | None = None

    def start(self) -> None:
        """Schedule one background subscription on the caller's running loop."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._connected = False
        self._quotes.clear()
        if self._journal is not None:
            self._journal.record("stopped")

    def get(self, instrument_token: str) -> Quote | None:
        """Return a fresh copy, or None after staleness or disconnection."""
        entry = self._quotes.get(instrument_token)
        if not self._connected or entry is None:
            return None
        quote, received_at = entry
        if monotonic() - received_at > self._max_age:
            self._quotes.pop(instrument_token, None)
            if self._journal is not None:
                self._journal.record("stale", quote)
            return None
        return quote.model_copy(deep=True)

    def status(self) -> SharedFeedStatus:
        for key in tuple(self._quotes):
            self.get(key)
        return SharedFeedStatus(
            running=self._task is not None and not self._task.done(),
            connected=self._connected,
            instrument_count=len(self._keys),
            cached_count=len(self._quotes),
            reconnects=self._reconnects,
            last_received_at=self._last_received_at,
        )

    async def _consume(self) -> None:
        attempt = 0
        while True:
            try:
                async for quote in self._provider.subscribe(self._keys):
                    if quote.instrument_token not in self._keys:
                        continue
                    if self._journal is not None:
                        if not self._connected:
                            self._journal.record("connected")
                        previous = self._quotes.get(quote.instrument_token)
                        if previous is not None and monotonic() - previous[1] > self._max_age:
                            self._journal.record("stale", previous[0])
                        self._journal.record("quote", quote)
                    self._connected = True
                    self._quotes[quote.instrument_token] = (
                        quote.model_copy(deep=True), monotonic()
                    )
                    self._last_received_at = datetime.now(timezone.utc)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Provider errors may include private connection URLs. Do not log
                # or expose them through feed status.
                pass
            finally:
                self._connected = False
                self._quotes.clear()
                if self._journal is not None:
                    self._journal.record("disconnected")
            attempt += 1
            self._reconnects = attempt
            if self._journal is not None:
                self._journal.record("reconnect")
            await asyncio.sleep(self._reconnect_delay)
