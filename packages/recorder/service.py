"""Session-aware Feed V3 recording loop: subscribe, persist raw frames and minute rows.

Read-only: the only network calls are market-data GETs and the Feed V3 WebSocket.
The authorized feed URL and token are secrets and are never logged or stored.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, TypeVar
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError

from packages.market_data.contracts import ExchangeSession, MarketDataProviderError
from packages.market_data.upstox import UpstoxMarketDataProvider
from packages.recorder.aggregator import MinuteAggregator
from packages.recorder.frames import FrameDecodeError, decode_frame
from packages.recorder.raw_store import RawFrameStore, iter_frames
from packages.recorder.storage import RecorderStorage
from packages.recorder.universe import (
    NIFTY_INDEX_KEY,
    RecordedSpec,
    UniverseConfig,
    discover,
    needs_recenter,
)

logger = logging.getLogger("recorder")
INDIA = ZoneInfo("Asia/Kolkata")
SUBSCRIBE_BATCH = 200
T = TypeVar("T")


class FeedSocket(Protocol):
    async def send(self, message: str | bytes) -> None: ...

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


SocketFactory = Callable[[str], AbstractAsyncContextManager[FeedSocket]]
Clock = Callable[[], datetime]


def _default_socket(uri: str) -> AbstractAsyncContextManager[FeedSocket]:
    from websockets.asyncio.client import connect

    return connect(uri, proxy=None, open_timeout=10, max_size=16_000_000, ping_interval=20)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RecorderConfig:
    data_dir: Path
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    lead_minutes: int = 5
    tail_minutes: int = 2
    recenter_check_seconds: float = 300.0
    reconnect_min_seconds: float = 2.0
    reconnect_max_seconds: float = 60.0


class SessionRecorder:
    """Record one NFO session; reconnect until the session (plus tail) ends."""

    def __init__(
        self,
        provider: UpstoxMarketDataProvider,
        storage: RecorderStorage,
        config: RecorderConfig,
        session_window: ExchangeSession,
        *,
        socket_factory: SocketFactory = _default_socket,
        clock: Clock = _utcnow,
    ) -> None:
        self._provider = provider
        self._storage = storage
        self._config = config
        self._session = session_window
        self._socket_factory = socket_factory
        self._clock = clock
        self._raw = RawFrameStore(config.data_dir / "feed_v3")
        self._minutes = MinuteAggregator()
        self._specs: dict[str, RecordedSpec] = {}
        self._spot: Decimal | None = None
        self.decode_errors = 0
        self.database_degraded = False

    @property
    def stop_at(self) -> datetime:
        return self._session.end + timedelta(minutes=self._config.tail_minutes)

    async def run(self) -> None:
        self._event("STARTED", session_start=self._session.start.isoformat(),
                    session_end=self._session.end.isoformat())
        try:
            await self._refresh_universe(initial=True)
            delay = self._config.reconnect_min_seconds
            while self._clock() < self.stop_at:
                try:
                    await self._connect_and_record()
                    delay = self._config.reconnect_min_seconds
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    # Exception text may include the authorized URL; record only its type.
                    self._event("DISCONNECTED", error=type(error).__name__)
                    logger.warning("Feed disconnected (%s); retrying", type(error).__name__)
                if self._clock() >= self.stop_at:
                    break
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._config.reconnect_max_seconds)
        finally:
            self._flush(final=True)
            self._raw.close()
            summary = self._db(
                "summary", self._storage.write_summary,
                session_start=self._session.start, session_end=self._session.end,
                index_key=NIFTY_INDEX_KEY, frames=self._raw.frames, raw_bytes=self._raw_bytes(),
            )
            if summary is not None:
                self._event(
                    "STOPPED", connected_minutes=summary.connected_minutes,
                    index_minutes=summary.index_minutes, rows=summary.snapshot_rows,
                    decode_errors=self.decode_errors,
                )
            logger.info("Session done: %s frames, %s decode errors, database %s",
                        self._raw.frames, self.decode_errors,
                        "degraded" if self.database_degraded else "ok")

    def _db(self, what: str, call: Callable[..., T], *args: Any, **kwargs: Any) -> T | None:
        """Database trouble must never stop raw capture; minute rows are rebuildable."""
        try:
            return call(*args, **kwargs)
        except SQLAlchemyError as error:
            if not self.database_degraded:
                logger.error("Database write failed (%s: %s); raw capture continues",
                             what, type(error).__name__)
            self.database_degraded = True
            return None

    def _event(self, kind: str, **detail: Any) -> None:
        self._db("event", self._storage.event, kind, **detail)

    async def _refresh_universe(self, *, initial: bool = False) -> list[RecordedSpec]:
        spot = self._spot
        if spot is None:
            spot = (await self._provider.get_quote(NIFTY_INDEX_KEY)).last_price
        specs = await discover(self._provider, self._storage.trading_day, spot,
                               self._config.universe)
        new = [spec for spec in specs if spec.instrument_key not in self._specs]
        if new:
            at = self._clock()
            write_universe_file(self._day_dir(), new, at)
            self._db("instruments", self._storage.write_instruments, new, at)
            self._specs.update({spec.instrument_key: spec for spec in new})
            self._event("UNIVERSE" if initial else "RECENTERED",
                        added=len(new), total=len(self._specs), spot=str(spot))
        return new

    async def _connect_and_record(self) -> None:
        uri = await self._provider.authorize_feed_url()
        async with self._socket_factory(uri) as socket:
            await self._subscribe(socket, list(self._specs))
            self._event("CONNECTED", instruments=len(self._specs))
            reader = asyncio.ensure_future(self._read(socket))
            ticker = asyncio.ensure_future(self._housekeeping(socket))
            try:
                done, _ = await asyncio.wait(
                    {reader, ticker}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()
            finally:
                for task in (reader, ticker):
                    task.cancel()
                await asyncio.gather(reader, ticker, return_exceptions=True)

    async def _subscribe(self, socket: FeedSocket, keys: list[str]) -> None:
        for start in range(0, len(keys), SUBSCRIBE_BATCH):
            await socket.send(json.dumps({
                "guid": uuid4().hex, "method": "sub",
                "data": {"mode": "full", "instrumentKeys": keys[start:start + SUBSCRIBE_BATCH]},
            }).encode("utf-8"))

    async def _read(self, socket: FeedSocket) -> None:
        async for payload in socket:
            received_at = self._clock()
            if not isinstance(payload, bytes):
                continue
            self._raw.append(payload, received_at)
            try:
                frame = decode_frame(payload, received_at)
            except FrameDecodeError:
                self.decode_errors += 1
                continue
            for tick in frame.ticks:
                if tick.instrument_key in self._specs:
                    self._minutes.add(tick)
                    if tick.instrument_key == NIFTY_INDEX_KEY and tick.ltp is not None:
                        self._spot = tick.ltp
        raise ConnectionError("Feed closed by server")

    async def _housekeeping(self, socket: FeedSocket) -> None:
        last_recenter = self._clock()
        while self._clock() < self.stop_at:
            await asyncio.sleep(1.0)
            self._flush()
            now = self._clock()
            due = (now - last_recenter).total_seconds() >= self._config.recenter_check_seconds
            if self._spot is not None and due:
                last_recenter = now
                if needs_recenter(self._specs.values(), self._spot, self._config.universe):
                    new = await self._refresh_universe()
                    if new:
                        await self._subscribe(socket, [spec.instrument_key for spec in new])

    def _flush(self, *, final: bool = False) -> None:
        bars = self._minutes.drain_all() if final else self._minutes.drain_completed(self._clock())
        if bars:
            self._db("snapshots", self._storage.write_snapshots,
                     [bar.row(self._storage.trading_day) for bar in bars])
        self._raw.flush()

    def _day_dir(self) -> Path:
        return self._config.data_dir / "feed_v3" / self._session.start.strftime("%Y-%m-%d")

    def _raw_bytes(self) -> int:
        return sum(path.stat().st_size for path in self._day_dir().glob("*.frames.gz"))


def write_universe_file(day_dir: Path, specs: list[RecordedSpec], at: datetime) -> None:
    """Append subscribed contract metadata next to the raw frames (JSON lines)."""
    day_dir.mkdir(parents=True, exist_ok=True)
    with (day_dir / "universe.jsonl").open("a", encoding="utf-8") as handle:
        for spec in specs:
            handle.write(json.dumps({
                "instrument_key": spec.instrument_key, "kind": spec.kind,
                "trading_symbol": spec.trading_symbol,
                "expiry": spec.expiry.isoformat() if spec.expiry else None,
                "strike": str(spec.strike) if spec.strike is not None else None,
                "option_type": spec.option_type, "lot_size": spec.lot_size,
                "tick_size": str(spec.tick_size) if spec.tick_size is not None else None,
                "first_subscribed_at": at.isoformat(),
            }) + "\n")


def read_universe_file(day_dir: Path) -> list[tuple[RecordedSpec, datetime]]:
    path = day_dir / "universe.jsonl"
    if not path.exists():
        return []
    rows: list[tuple[RecordedSpec, datetime]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        rows.append((RecordedSpec(
            instrument_key=item["instrument_key"], kind=item["kind"],
            trading_symbol=item["trading_symbol"],
            expiry=date.fromisoformat(item["expiry"]) if item["expiry"] else None,
            strike=Decimal(item["strike"]) if item["strike"] is not None else None,
            option_type=item["option_type"], lot_size=item["lot_size"],
            tick_size=Decimal(item["tick_size"]) if item["tick_size"] is not None else None,
        ), datetime.fromisoformat(item["first_subscribed_at"])))
    return rows


def rebuild_day(day_dir: Path, storage: RecorderStorage) -> tuple[int, int]:
    """Re-derive minute rows and instruments for one day from raw files. Idempotent."""
    universe = read_universe_file(day_dir)
    for spec, at in universe:
        storage.write_instruments([spec], at)
    keys = {spec.instrument_key for spec, _ in universe}
    frames = rows = 0
    for path in sorted(day_dir.glob("*.frames.gz")):
        aggregator = MinuteAggregator()
        for received_at, payload in iter_frames(path):
            frames += 1
            try:
                decoded = decode_frame(payload, received_at)
            except FrameDecodeError:
                continue
            for tick in decoded.ticks:
                if not keys or tick.instrument_key in keys:
                    aggregator.add(tick)
        bars = aggregator.drain_all()
        storage.write_snapshots([bar.row(storage.trading_day) for bar in bars])
        rows += len(bars)
    return frames, rows


async def run_forever(
    provider: UpstoxMarketDataProvider,
    storage_for: Callable[[date], RecorderStorage],
    config: RecorderConfig,
    *,
    clock: Clock = _utcnow,
    socket_factory: SocketFactory = _default_socket,
) -> None:
    """Record every NFO session; sleep across nights, weekends and holidays."""
    while True:
        now = clock()
        today = now.astimezone(INDIA).date()
        try:
            window = await provider.get_nfo_session(today)
        except MarketDataProviderError as error:
            logger.warning("Session lookup failed (%s); retrying in 5 minutes",
                           type(error).__name__)
            await asyncio.sleep(300)
            continue
        if window is None or now >= window.end + timedelta(minutes=config.tail_minutes):
            logger.info("No session left today; next check at 08:30 IST tomorrow")
            await sleep_until(now + timedelta(seconds=_seconds_until_next_check(now)), clock)
            continue
        start_at = window.start - timedelta(minutes=config.lead_minutes)
        if now < start_at:
            logger.info("Next session %s; waiting", window.start.astimezone(INDIA))
            await sleep_until(start_at, clock)
            continue
        logger.info("Recording NFO session %s", today)
        try:
            await SessionRecorder(
                provider, storage_for(today), config, window,
                socket_factory=socket_factory, clock=clock,
            ).run()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Never log exception text: provider errors can carry private URLs.
            logger.error("Session recording failed (%s); retrying in 60 s",
                         type(error).__name__)
            await asyncio.sleep(60)


async def sleep_until(target: datetime, clock: Clock, *, step_seconds: float = 30.0) -> None:
    """Wait for a wall-clock time in short steps.

    One long asyncio.sleep uses the monotonic clock, which stops while a laptop or
    Docker Desktop VM is suspended; the recorder then wakes hours late and misses the
    open. Re-reading the wall clock every step bounds that delay to one step.
    """
    while (remaining := (target - clock()).total_seconds()) > 0:
        await asyncio.sleep(min(step_seconds, remaining))


def _seconds_until_next_check(now: datetime) -> float:
    local = now.astimezone(INDIA)
    next_check = (local + timedelta(days=1)).replace(hour=8, minute=30, second=0, microsecond=0)
    return max(60.0, (next_check - local).total_seconds())
