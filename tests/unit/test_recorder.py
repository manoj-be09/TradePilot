import asyncio
import gzip
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from packages.database import models  # noqa: F401 - register shared metadata
from packages.database.base import Base
from packages.database.recorder_models import (
    MarketSnapshot1m,
    RecordedInstrument,
    RecorderDaySummary,
    RecorderEvent,
)
from packages.market_data import upstox_v3_pb2 as pb
from packages.market_data.contracts import DiscoveredInstrument, ExchangeSession, OptionContract
from packages.recorder.aggregator import MinuteAggregator
from packages.recorder.frames import FrameDecodeError, decode_frame
from packages.recorder.raw_store import RawFrameStore, iter_frames
from packages.recorder.service import RecorderConfig, SessionRecorder, rebuild_day
from packages.recorder.storage import RecorderStorage
from packages.recorder.universe import (
    INDIA_VIX_KEY,
    NIFTY_INDEX_KEY,
    UniverseConfig,
    needs_recenter,
    nifty_futures,
    select_options,
    strike_window,
)

T0 = datetime(2026, 10, 27, 4, 0, 5, tzinfo=timezone.utc)
DAY = date(2026, 10, 27)


def _frame(feeds: dict[str, Any], *, ts_ms: int = 1_793_073_605_000) -> bytes:
    response = pb.FeedResponse(type=pb.live_feed, currentTs=ts_ms)
    for key, feed in feeds.items():
        response.feeds[key].CopyFrom(feed)
    return response.SerializeToString()


def _option_feed(ltp: float, *, bid: float = 0.0, ask: float = 0.0, oi: float = 0.0) -> Any:
    market = pb.MarketFullFeed(
        ltpc=pb.LTPC(ltp=ltp, ltt=1_793_073_604_000, ltq=65, cp=110.5),
        marketLevel=pb.MarketLevel(bidAskQuote=[
            pb.Quote(bidQ=650, bidP=bid, askQ=325, askP=ask),
            pb.Quote(bidQ=130, bidP=bid - 0.05, askQ=195, askP=ask + 0.05),
        ]),
        optionGreeks=pb.OptionGreeks(delta=0.45, theta=-12.5, gamma=0.001, vega=8.2, rho=0.3),
        vtt=123_456, oi=oi, iv=0.1342, atp=101.25, tbq=1000, tsq=900,
    )
    return pb.Feed(fullFeed=pb.FullFeed(marketFF=market))


def _index_feed(ltp: float) -> Any:
    return pb.Feed(fullFeed=pb.FullFeed(indexFF=pb.IndexFullFeed(ltpc=pb.LTPC(ltp=ltp))))


# --- frames -----------------------------------------------------------------

def test_decode_full_option_feed_keeps_depth_greeks_and_oi() -> None:
    frame = decode_frame(
        _frame({"NSE_FO|1": _option_feed(101.5, bid=101.4, ask=101.6, oi=5_000_000)}), T0
    )
    (tick,) = frame.ticks
    assert frame.kind == "live_feed"
    assert tick.ltp == Decimal("101.50") and tick.prev_close == Decimal("110.50")
    assert (tick.bid, tick.ask, tick.bid_qty, tick.ask_qty) == (
        Decimal("101.40"), Decimal("101.60"), 650, 325
    )
    assert len(tick.depth) == 2
    assert tick.open_interest == Decimal("5000000.0")
    assert tick.iv == Decimal("0.1342") and tick.theta == Decimal("-12.5")
    assert tick.volume == 123_456 and tick.last_trade_qty == 65
    assert tick.provider_ts is not None and tick.received_at == T0


def test_unset_protobuf_zeros_are_stored_as_missing() -> None:
    (tick,) = decode_frame(_frame({"NSE_FO|1": _option_feed(50.0)}), T0).ticks
    assert tick.bid is None and tick.ask is None and tick.open_interest is None


def test_index_feed_and_invalid_frames() -> None:
    (tick,) = decode_frame(_frame({NIFTY_INDEX_KEY: _index_feed(25_500.15)}), T0).ticks
    assert tick.ltp == Decimal("25500.15") and tick.bid is None
    with pytest.raises(FrameDecodeError):
        decode_frame(b"\xff\xff\xff", T0)
    with pytest.raises(ValueError):
        decode_frame(b"", T0.replace(tzinfo=None))


# --- aggregator -------------------------------------------------------------

def test_minute_bars_have_ltp_ohlc_and_carry_missing_fields() -> None:
    agg = MinuteAggregator()
    prices = [(0, 100.0, 99.9), (10, 104.0, 0.0), (20, 98.0, 97.9), (40, 101.0, 0.0)]
    for offset, ltp, bid in prices:
        for tick in decode_frame(
            _frame({"NSE_FO|1": _option_feed(ltp, bid=bid, ask=ltp + 0.1)}),
            T0 + timedelta(seconds=offset),
        ).ticks:
            agg.add(tick)
    assert agg.drain_completed(T0 + timedelta(seconds=50)) == []
    (bar,) = agg.drain_completed(T0 + timedelta(seconds=58))
    row = bar.row(DAY)
    assert (row["ltp_open"], row["ltp_high"], row["ltp_low"], row["ltp_close"]) == (
        Decimal("100.00"), Decimal("104.00"), Decimal("98.00"), Decimal("101.00")
    )
    assert row["bid"] == Decimal("97.90")  # last non-empty bid in the minute
    assert row["message_count"] == 4
    assert row["bucket_start"] == datetime(2026, 10, 27, 4, 0, tzinfo=timezone.utc)
    assert row["depth"]["levels"][0][0] == 650


# --- raw store --------------------------------------------------------------

def test_raw_store_round_trip_across_hours(tmp_path: Path) -> None:
    store = RawFrameStore(tmp_path)
    late = T0.replace(minute=59, second=59)
    store.append(b"first", late)
    store.append(b"second", late + timedelta(seconds=2))
    store.close()
    files = sorted(tmp_path.rglob("*.frames.gz"))
    assert [path.name for path in files] == ["04.frames.gz", "05.frames.gz"]
    assert [payload for _, payload in iter_frames(files[0])] == [b"first"]
    ((stamp, payload),) = list(iter_frames(files[1]))
    assert payload == b"second" and stamp == late + timedelta(seconds=2)


def test_raw_store_appends_and_tolerates_truncated_tail(tmp_path: Path) -> None:
    for payload in (b"a" * 10, b"b" * 20):
        store = RawFrameStore(tmp_path)
        store.append(payload, T0)
        store.close()
    (path,) = tmp_path.rglob("*.frames.gz")
    assert [len(p) for _, p in iter_frames(path)] == [10, 20]
    data = path.read_bytes()
    path.write_bytes(data[:-6])
    assert [len(p) for _, p in iter_frames(path)][:1] == [10]
    with gzip.open(path, "rb") as handle:
        assert handle.read(1)


# --- universe ---------------------------------------------------------------

def _contracts(expiries: list[date], strikes: range) -> list[OptionContract]:
    return [
        OptionContract(
            instrument_key=f"NSE_FO|{expiry:%m%d}{strike}{side}", trading_symbol="x",
            expiry=expiry, strike=Decimal(strike), option_type=side, lot_size=65,
            tick_size=Decimal("0.05"),
        )
        for expiry in expiries for strike in strikes for side in ("CE", "PE")
    ]


def test_strike_window_centres_on_atm() -> None:
    strikes = [Decimal(s) for s in range(24_000, 27_050, 50)]
    window = strike_window(strikes, Decimal("25512"), 2)
    assert window == [Decimal(s) for s in (25_400, 25_450, 25_500, 25_550, 25_600)]
    assert strike_window(strikes, Decimal("23000"), 2)[0] == Decimal("24000")
    assert strike_window([], Decimal("1"), 2) == []


def test_select_options_uses_weeklies_and_monthlies() -> None:
    weeklies = [DAY, date(2026, 11, 3), date(2026, 11, 9), date(2026, 11, 17)]
    monthly = date(2026, 11, 23)
    contracts = _contracts([date(2026, 10, 20), *weeklies, monthly], range(24_000, 27_050, 50))
    specs = select_options(
        contracts, today=DAY, spot=Decimal("25500"), monthly_expiries=[monthly],
        config=UniverseConfig(weekly_expiries=3, strikes_each_side=2),
    )
    assert sorted({spec.expiry for spec in specs}) == [*weeklies[:3], monthly]
    assert len(specs) == 4 * 5 * 2
    assert not needs_recenter(specs, Decimal("25500"), UniverseConfig(recenter_margin=1))
    assert needs_recenter(specs, Decimal("25600"), UniverseConfig(recenter_margin=1))


def test_nifty_futures_excludes_other_indices_and_expired() -> None:
    rows = [
        DiscoveredInstrument(instrument_key=key, trading_symbol=symbol, exchange="NSE_FO",
                             segment="NSE_FO", instrument_type="FUT", expiry=expiry, lot_size=65)
        for key, symbol, expiry in [
            ("NSE_FO|1", "NIFTY FUT 23 NOV 26", date(2026, 11, 23)),
            ("NSE_FO|2", "NIFTYNXT50 FUT 23 NOV 26", date(2026, 11, 23)),
            ("NSE_FO|3", "NIFTY FUT 27 OCT 26", DAY),
            ("NSE_FO|4", "NIFTY FUT 29 SEP 26", date(2026, 9, 29)),
        ]
    ]
    assert [spec.instrument_key for spec in nifty_futures(rows, DAY)] == ["NSE_FO|3", "NSE_FO|1"]


# --- storage and service ------------------------------------------------------

def _factory() -> sessionmaker[Any]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_snapshot_writes_are_idempotent() -> None:
    factory = _factory()
    storage = RecorderStorage(factory, DAY)
    agg = MinuteAggregator()
    for tick in decode_frame(_frame({"NSE_FO|1": _option_feed(10.0)}), T0).ticks:
        agg.add(tick)
    rows = [bar.row(DAY) for bar in agg.drain_all()]
    storage.write_snapshots(rows)
    storage.write_snapshots(rows)
    with factory() as session:
        assert len(session.scalars(select(MarketSnapshot1m)).all()) == 1


class _FakeProvider:
    def __init__(self) -> None:
        self.authorizations = 0

    async def get_quote(self, key: str) -> Any:
        return SimpleNamespace(last_price=Decimal("25500"))

    async def search_instruments(self, query: str, **_: Any) -> list[DiscoveredInstrument]:
        return [DiscoveredInstrument(
            instrument_key="NSE_FO|F1", trading_symbol="NIFTY FUT 23 NOV 26", exchange="NSE_FO",
            segment="NSE_FO", instrument_type="FUT", expiry=date(2026, 11, 23), lot_size=65,
        )]

    async def get_option_contracts(self, underlying: str) -> list[OptionContract]:
        return _contracts([DAY, date(2026, 11, 23)], range(25_000, 26_050, 50))

    async def authorize_feed_url(self) -> str:
        self.authorizations += 1
        return "wss://example.invalid/feed"


def _run_recorder(tmp_path: Path, factory: sessionmaker[Any]) -> tuple[list[bytes], str]:
    now = datetime.now(timezone.utc)
    window = ExchangeSession(exchange="NFO", start=now - timedelta(minutes=1),
                             end=now + timedelta(seconds=2))
    sent: list[bytes] = []
    option_key = f"NSE_FO|{DAY:%m%d}25500CE"

    class _Socket:
        async def send(self, message: str | bytes) -> None:
            sent.append(message if isinstance(message, bytes) else message.encode())

        def __aiter__(self) -> AsyncIterator[bytes]:
            async def frames() -> AsyncIterator[bytes]:
                yield _frame({NIFTY_INDEX_KEY: _index_feed(25_510.0),
                              option_key: _option_feed(101.0, bid=100.9, ask=101.1)})
                yield _frame({INDIA_VIX_KEY: _index_feed(12.3), "NSE_FO|NOT_SUBSCRIBED":
                              _option_feed(5.0)})
                await asyncio.sleep(3600)
            return frames()

    @asynccontextmanager
    async def socket_factory(uri: str) -> AsyncIterator[_Socket]:
        assert uri.startswith("wss://")
        yield _Socket()

    provider = _FakeProvider()
    recorder = SessionRecorder(
        cast(Any, provider), RecorderStorage(factory, DAY),
        RecorderConfig(data_dir=tmp_path, tail_minutes=0,
                       universe=UniverseConfig(strikes_each_side=2)),
        window, socket_factory=socket_factory,
    )
    asyncio.run(asyncio.wait_for(recorder.run(), timeout=15))
    return sent, option_key


def test_session_recorder_end_to_end(tmp_path: Path) -> None:
    factory = _factory()
    sent, option_key = _run_recorder(tmp_path, factory)
    with factory() as session:
        keys = set(session.scalars(select(MarketSnapshot1m.instrument_key)))
        assert keys == {NIFTY_INDEX_KEY, INDIA_VIX_KEY, option_key}
        kinds = [event.kind for event in session.scalars(
            select(RecorderEvent).order_by(RecorderEvent.occurred_at))]
        assert kinds[:3] == ["STARTED", "UNIVERSE", "CONNECTED"] and kinds[-1] == "STOPPED"
        instruments = session.scalars(select(RecordedInstrument)).all()
        assert len(instruments) == 2 + 1 + 2 * 5 * 2
        summary = session.get(RecorderDaySummary, DAY)
        assert summary is not None and summary.index_minutes >= 1 and summary.frames == 2
    assert b'"mode": "full"' in sent[0]
    assert list(tmp_path.rglob("*.frames.gz"))


def test_raw_capture_continues_without_database_and_rebuilds_later(tmp_path: Path) -> None:
    missing_tables = sessionmaker(bind=create_engine("sqlite://"), expire_on_commit=False)
    _, option_key = _run_recorder(tmp_path, missing_tables)
    (day_dir,) = (tmp_path / "feed_v3").iterdir()
    assert (day_dir / "universe.jsonl").exists() and list(day_dir.glob("*.frames.gz"))

    factory = _factory()
    storage = RecorderStorage(factory, DAY)
    frames, rows = rebuild_day(day_dir, storage)
    assert (frames, rows) == (2, 3)
    assert rebuild_day(day_dir, storage) == (2, 3)  # idempotent
    with factory() as session:
        keys = set(session.scalars(select(MarketSnapshot1m.instrument_key)))
        assert keys == {NIFTY_INDEX_KEY, INDIA_VIX_KEY, option_key}
        assert len(session.scalars(select(RecordedInstrument)).all()) == 2 + 1 + 2 * 5 * 2
