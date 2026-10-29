"""Decode Feed V3 frames into per-instrument observations without losing fields.

Protobuf 3 reports unset numbers as zero. Prices, quantities and analytics that are
zero or non-finite are kept as None so "no data" is never stored as a real zero.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from math import isfinite
from typing import Any

from google.protobuf.message import DecodeError

from packages.market_data import upstox_v3_pb2

MAX_DEPTH_LEVELS = 5


class FrameDecodeError(ValueError):
    pass


@dataclass(frozen=True)
class Tick:
    instrument_key: str
    received_at: datetime
    provider_ts: datetime | None
    ltp: Decimal | None
    last_trade_time: datetime | None
    last_trade_qty: int | None
    prev_close: Decimal | None
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_qty: int | None = None
    ask_qty: int | None = None
    depth: tuple[tuple[int, str, int, str], ...] = ()
    volume: int | None = None
    open_interest: Decimal | None = None
    iv: Decimal | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    rho: Decimal | None = None
    avg_traded_price: Decimal | None = None
    total_buy_qty: Decimal | None = None
    total_sell_qty: Decimal | None = None


@dataclass(frozen=True)
class DecodedFrame:
    kind: str  # "initial_feed", "live_feed" or "market_info"
    provider_ts: datetime | None
    ticks: tuple[Tick, ...]


def decode_frame(payload: bytes, received_at: datetime) -> DecodedFrame:
    if received_at.tzinfo is None:
        raise ValueError("Frame receipt time must have a timezone")
    try:
        frame = upstox_v3_pb2.FeedResponse.FromString(payload)
    except DecodeError as error:
        raise FrameDecodeError("Invalid Feed V3 frame") from error
    provider_ts = _ms_time(frame.currentTs)
    if frame.type == upstox_v3_pb2.market_info:
        return DecodedFrame("market_info", provider_ts, ())
    kind = "initial_feed" if frame.type == upstox_v3_pb2.initial_feed else "live_feed"
    ticks = tuple(
        tick for key, feed in frame.feeds.items()
        if (tick := _tick(key, feed, received_at, provider_ts)) is not None
    )
    return DecodedFrame(kind, provider_ts, ticks)


def _tick(key: str, feed: Any, received_at: datetime, provider_ts: datetime | None) -> Tick | None:
    mode = feed.WhichOneof("FeedUnion")
    if mode == "ltpc":
        return _base(key, feed.ltpc, received_at, provider_ts)
    if mode == "firstLevelWithGreeks":
        first = feed.firstLevelWithGreeks
        levels = [first.firstDepth] if first.HasField("firstDepth") else []
        greeks = first.optionGreeks if first.HasField("optionGreeks") else None
        return _market(key, first.ltpc, levels, greeks, first, received_at, provider_ts)
    if mode != "fullFeed":
        return None
    full = feed.fullFeed
    branch = full.WhichOneof("FullFeedUnion")
    if branch == "indexFF":
        return _base(key, full.indexFF.ltpc, received_at, provider_ts)
    if branch != "marketFF":
        return None
    market = full.marketFF
    greeks = market.optionGreeks if market.HasField("optionGreeks") else None
    return _market(
        key, market.ltpc, list(market.marketLevel.bidAskQuote), greeks, market,
        received_at, provider_ts,
    )


def _base(key: str, ltpc: Any, received_at: datetime, provider_ts: datetime | None) -> Tick:
    return Tick(
        instrument_key=key, received_at=received_at, provider_ts=provider_ts,
        ltp=_price(ltpc.ltp), last_trade_time=_ms_time(ltpc.ltt),
        last_trade_qty=_qty(ltpc.ltq), prev_close=_price(ltpc.cp),
    )


def _market(
    key: str, ltpc: Any, levels: list[Any], greeks: Any, body: Any,
    received_at: datetime, provider_ts: datetime | None,
) -> Tick:
    base = _base(key, ltpc, received_at, provider_ts)
    depth = tuple(
        (int(level.bidQ), repr(level.bidP), int(level.askQ), repr(level.askP))
        for level in levels[:MAX_DEPTH_LEVELS]
    )
    best = levels[0] if levels else None
    return Tick(
        instrument_key=base.instrument_key, received_at=base.received_at,
        provider_ts=base.provider_ts, ltp=base.ltp, last_trade_time=base.last_trade_time,
        last_trade_qty=base.last_trade_qty, prev_close=base.prev_close,
        bid=_price(best.bidP) if best is not None else None,
        ask=_price(best.askP) if best is not None else None,
        bid_qty=_qty(best.bidQ) if best is not None else None,
        ask_qty=_qty(best.askQ) if best is not None else None,
        depth=depth,
        volume=_qty(body.vtt),
        open_interest=_positive(getattr(body, "oi", 0)),
        iv=_positive(getattr(body, "iv", 0)),
        delta=_number(greeks.delta) if greeks is not None else None,
        gamma=_number(greeks.gamma) if greeks is not None else None,
        theta=_number(greeks.theta) if greeks is not None else None,
        vega=_number(greeks.vega) if greeks is not None else None,
        rho=_number(greeks.rho) if greeks is not None else None,
        avg_traded_price=_price(getattr(body, "atp", 0)),
        total_buy_qty=_positive(getattr(body, "tbq", 0)),
        total_sell_qty=_positive(getattr(body, "tsq", 0)),
    )


def _number(value: float) -> Decimal | None:
    return Decimal(repr(value)) if isfinite(value) else None


def _positive(value: float) -> Decimal | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _price(value: float) -> Decimal | None:
    number = _positive(value)
    return number.quantize(Decimal("0.01")) if number is not None else None


def _qty(value: int) -> int | None:
    return int(value) if value > 0 else None


def _ms_time(value: int) -> datetime | None:
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
