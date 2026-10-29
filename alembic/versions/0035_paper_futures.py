"""Add NIFTY futures paper trades and orders on the shared F&O paper account."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0035_paper_futures"
down_revision = "0034_feed_v3_recorder"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_MONEY = sa.Numeric(14, 2)
_PRICE = sa.Numeric(12, 2)


def upgrade() -> None:
    op.create_table(
        "paper_futures_trades",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("paper_accounts.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("instrument_key", sa.String(length=160), nullable=False),
        sa.Column("trading_symbol", sa.String(length=128), nullable=False),
        sa.Column("underlying", sa.String(length=32), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("lots", sa.Integer(), nullable=False),
        sa.Column("lot_size", sa.Integer(), nullable=False),
        sa.Column("entry_price", _PRICE, nullable=False),
        sa.Column("margin_blocked", _MONEY, nullable=False),
        sa.Column("entry_cash_flow", _MONEY, nullable=False),
        sa.Column("max_loss", _MONEY, nullable=False),
        sa.Column("stop_loss_amount", _MONEY, nullable=False),
        sa.Column("target_profit_amount", _MONEY, nullable=True),
        sa.Column("trailing_stop_amount", _MONEY, nullable=True),
        sa.Column("peak_unrealized_pnl", _MONEY, nullable=False, server_default="0"),
        sa.Column("last_mark_price", _PRICE, nullable=True),
        sa.Column("last_marked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unrealized_pnl", _MONEY, nullable=True),
        sa.Column("exit_price", _PRICE, nullable=True),
        sa.Column("realized_pnl", _MONEY, nullable=True),
        sa.Column("exit_reason", sa.String(length=24), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="OPEN"),
        sa.Column("entry_snapshot", _JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_paper_futures_trade_command"),
        sa.CheckConstraint("side IN ('LONG', 'SHORT')", name="ck_paper_futures_side"),
        sa.CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_paper_futures_status"),
        sa.CheckConstraint("lots > 0", name="ck_paper_futures_lots_positive"),
        sa.CheckConstraint("lot_size > 0", name="ck_paper_futures_lot_size_positive"),
        sa.CheckConstraint("entry_price > 0", name="ck_paper_futures_entry_price_positive"),
        sa.CheckConstraint("margin_blocked > 0", name="ck_paper_futures_margin_positive"),
        sa.CheckConstraint("stop_loss_amount > 0", name="ck_paper_futures_stop_positive"),
        sa.CheckConstraint(
            "entry_cash_flow = -margin_blocked", name="ck_paper_futures_entry_flow"
        ),
    )
    op.create_index(
        "ix_paper_futures_trades_account_id", "paper_futures_trades", ["account_id"]
    )
    op.create_index("ix_paper_futures_trades_user_id", "paper_futures_trades", ["user_id"])
    op.create_index(
        "ix_paper_futures_account_status", "paper_futures_trades", ["account_id", "status"]
    )
    op.create_table(
        "paper_futures_orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("paper_accounts.id"), nullable=False),
        sa.Column(
            "trade_id", sa.Uuid(), sa.ForeignKey("paper_futures_trades.id"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="EXECUTED"),
        sa.Column("net_cash_flow", _MONEY, nullable=False),
        sa.Column("fill_snapshot", _JSON, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("trade_id", "action", name="uq_paper_futures_order_action"),
        sa.CheckConstraint("action IN ('OPEN', 'CLOSE')", name="ck_paper_futures_order_action"),
        sa.CheckConstraint("status = 'EXECUTED'", name="ck_paper_futures_order_status"),
    )
    op.create_index(
        "ix_paper_futures_orders_account_id", "paper_futures_orders", ["account_id"]
    )
    op.create_index("ix_paper_futures_orders_trade_id", "paper_futures_orders", ["trade_id"])
    op.create_index("ix_paper_futures_orders_user_id", "paper_futures_orders", ["user_id"])


def downgrade() -> None:
    op.drop_table("paper_futures_orders")
    op.drop_table("paper_futures_trades")
