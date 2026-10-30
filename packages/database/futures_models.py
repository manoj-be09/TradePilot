"""NIFTY futures paper positions; they share the F&O paper account cash ledger."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.base import Base
from packages.database.models import PortableJSON


class PaperFuturesTrade(Base):
    """One simulated futures position. Margin is blocked as an opening cash flow."""

    __tablename__ = "paper_futures_trades"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_paper_futures_trade_command"),
        UniqueConstraint("deployment_id", name="uq_paper_futures_deployment"),
        CheckConstraint("side IN ('LONG', 'SHORT')", name="ck_paper_futures_side"),
        CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_paper_futures_status"),
        CheckConstraint("lots > 0", name="ck_paper_futures_lots_positive"),
        CheckConstraint("lot_size > 0", name="ck_paper_futures_lot_size_positive"),
        CheckConstraint("entry_price > 0", name="ck_paper_futures_entry_price_positive"),
        CheckConstraint("margin_blocked > 0", name="ck_paper_futures_margin_positive"),
        CheckConstraint("stop_loss_amount > 0", name="ck_paper_futures_stop_positive"),
        CheckConstraint("entry_cash_flow = -margin_blocked", name="ck_paper_futures_entry_flow"),
        Index("ix_paper_futures_account_status", "account_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("paper_accounts.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    # Set for armed template fills; manual tickets have neither.
    strategy_key: Mapped[str | None] = mapped_column(String(64), index=True)
    deployment_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("paper_deployments.id"))
    provider: Mapped[str] = mapped_column(String(32))
    instrument_key: Mapped[str] = mapped_column(String(160))
    trading_symbol: Mapped[str] = mapped_column(String(128))
    underlying: Mapped[str] = mapped_column(String(32))
    expiry: Mapped[date] = mapped_column(Date)
    side: Mapped[str] = mapped_column(String(8))
    lots: Mapped[int] = mapped_column(Integer)
    lot_size: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    margin_blocked: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    entry_cash_flow: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    # The stop-loss amount is the risk budget used by the account caps. A gap through
    # the stop can lose more; the snapshot records that this is a modeled limit.
    max_loss: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    stop_loss_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    target_profit_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    trailing_stop_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    peak_unrealized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal(0), server_default="0"
    )
    last_mark_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    last_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    exit_reason: Mapped[str | None] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="OPEN", server_default="OPEN")
    entry_snapshot: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON, default=dict, server_default=text("'{}'")
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaperFuturesOrder(Base):
    __tablename__ = "paper_futures_orders"
    __table_args__ = (
        UniqueConstraint("trade_id", "action", name="uq_paper_futures_order_action"),
        CheckConstraint("action IN ('OPEN', 'CLOSE')", name="ck_paper_futures_order_action"),
        CheckConstraint("status = 'EXECUTED'", name="ck_paper_futures_order_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("paper_accounts.id"), index=True)
    trade_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("paper_futures_trades.id"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="EXECUTED", server_default="EXECUTED")
    net_cash_flow: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    fill_snapshot: Mapped[dict[str, Any]] = mapped_column(PortableJSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
