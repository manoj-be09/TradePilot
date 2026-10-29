"""Private historical NIFTY option candles imported from Upstox expired instruments.

Never redistributed (see DATA_SOURCE_DECISION.md). Candles carry OHLC, volume and open
interest only: there is no historical bid/ask, so spreads and fills must come from a
separately labelled model. Rows are replaced only by re-importing a whole contract.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.base import Base

_PRICE = Numeric(14, 2)


class HistoricalOptionBar(Base):
    __tablename__ = "historical_option_bars"
    __table_args__ = (
        Index("ix_historical_option_bars_expiry", "expiry", "interval", "strike"),
    )

    instrument_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), primary_key=True)
    bar_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    expiry: Mapped[date] = mapped_column(Date)
    strike: Mapped[Decimal] = mapped_column(_PRICE)
    option_type: Mapped[str] = mapped_column(String(2))
    open: Mapped[Decimal] = mapped_column(_PRICE)
    high: Mapped[Decimal] = mapped_column(_PRICE)
    low: Mapped[Decimal] = mapped_column(_PRICE)
    close: Mapped[Decimal] = mapped_column(_PRICE)
    volume: Mapped[int] = mapped_column(BigInteger)
    open_interest: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class HistoricalImportContract(Base):
    """Coverage and resume record: one row per imported contract and interval."""

    __tablename__ = "historical_import_contracts"
    __table_args__ = (Index("ix_historical_import_contracts_expiry", "expiry", "interval"),)

    instrument_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    trading_symbol: Mapped[str] = mapped_column(String(128))
    expiry: Mapped[date] = mapped_column(Date)
    strike: Mapped[Decimal] = mapped_column(_PRICE)
    option_type: Mapped[str] = mapped_column(String(2))
    lot_size: Mapped[int] = mapped_column(Integer)
    requested_from: Mapped[date] = mapped_column(Date)
    requested_to: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12))  # COMPLETE, EMPTY or FAILED
    bar_count: Mapped[int] = mapped_column(Integer)
    first_bar: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_bar: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[str] = mapped_column(String(300))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
