"""Private Feed V3 market-data recording tables (never redistributed; see DATA_SOURCE_DECISION.md).

Times are UTC. `received_at` is local receipt time and `provider_ts` is Upstox message
generation time; neither is an exchange-origin book timestamp.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.base import Base
from packages.database.models import PortableJSON

_PRICE = Numeric(14, 2)
_RATIO = Numeric(18, 8)


class MarketSnapshot1m(Base):
    """Last observed state of one instrument within one UTC minute, plus LTP OHLC."""

    __tablename__ = "market_snapshots_1m"
    __table_args__ = (Index("ix_market_snapshots_1m_day_key", "trading_day", "instrument_key"),)

    instrument_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    trading_day: Mapped[date] = mapped_column(Date)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provider_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_trade_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ltp_open: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    ltp_high: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    ltp_low: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    ltp_close: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    last_trade_qty: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    prev_close: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    bid: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    ask: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    bid_qty: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ask_qty: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    depth: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    iv: Mapped[Decimal | None] = mapped_column(_RATIO, nullable=True)
    delta: Mapped[Decimal | None] = mapped_column(_RATIO, nullable=True)
    gamma: Mapped[Decimal | None] = mapped_column(_RATIO, nullable=True)
    theta: Mapped[Decimal | None] = mapped_column(_RATIO, nullable=True)
    vega: Mapped[Decimal | None] = mapped_column(_RATIO, nullable=True)
    rho: Mapped[Decimal | None] = mapped_column(_RATIO, nullable=True)
    avg_traded_price: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    total_buy_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    total_sell_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    message_count: Mapped[int] = mapped_column(Integer)


class RecordedInstrument(Base):
    """Contract metadata as observed on a trading day (lot sizes and expiries change)."""

    __tablename__ = "recorded_instruments"

    trading_day: Mapped[date] = mapped_column(Date, primary_key=True)
    instrument_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))
    trading_symbol: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    strike: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(2), nullable=True)
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tick_size: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    first_subscribed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RecorderEvent(Base):
    __tablename__ = "recorder_events"
    __table_args__ = (Index("ix_recorder_events_day", "trading_day", "occurred_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    trading_day: Mapped[date] = mapped_column(Date)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(24))
    detail: Mapped[dict[str, Any]] = mapped_column(PortableJSON)


class RecorderDaySummary(Base):
    """Coverage of one recorded session; rewritten when the day's recording finishes."""

    __tablename__ = "recorder_day_summaries"

    trading_day: Mapped[date] = mapped_column(Date, primary_key=True)
    session_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    instruments: Mapped[int] = mapped_column(Integer)
    session_minutes: Mapped[int] = mapped_column(Integer)
    connected_minutes: Mapped[int] = mapped_column(Integer)
    index_minutes: Mapped[int] = mapped_column(Integer)
    snapshot_rows: Mapped[int] = mapped_column(Integer)
    frames: Mapped[int] = mapped_column(BigInteger)
    raw_bytes: Mapped[int] = mapped_column(BigInteger)
    disconnects: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
