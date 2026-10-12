"""Add isolated paper accounts, trades, and simulated fill records."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_paper_trading"
down_revision = "0001_initial_domain_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_accounts",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("initial_cash", sa.Numeric(14, 2), nullable=False),
        sa.Column("cash_balance", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "kill_switch_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("initial_cash > 0", name="ck_paper_initial_cash_positive"),
        sa.CheckConstraint("cash_balance >= 0", name="ck_paper_cash_balance_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "paper_trades",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_key", sa.String(length=64), nullable=False),
        sa.Column("strategy_name", sa.String(length=128), nullable=False),
        sa.Column("underlying", sa.String(length=32), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("reference_strike", sa.Numeric(12, 2), nullable=False),
        sa.Column("lots", sa.Integer(), nullable=False),
        sa.Column("lot_size", sa.Integer(), nullable=False),
        sa.Column("max_loss", sa.Numeric(14, 2), nullable=False),
        sa.Column("entry_cash_flow", sa.Numeric(14, 2), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(14, 2), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="OPEN", nullable=False),
        sa.Column("legs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("lots > 0", name="ck_paper_trade_lots_positive"),
        sa.CheckConstraint("lot_size > 0", name="ck_paper_trade_lot_size_positive"),
        sa.CheckConstraint("max_loss >= 0", name="ck_paper_trade_max_loss_nonnegative"),
        sa.CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_paper_trade_status"),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_trades_account_id", "paper_trades", ["account_id"])
    op.create_index("ix_paper_trades_user_id", "paper_trades", ["user_id"])
    op.create_table(
        "paper_orders",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("trade_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="EXECUTED", nullable=False),
        sa.Column("net_cash_flow", sa.Numeric(14, 2), nullable=False),
        sa.Column("fill_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("action IN ('OPEN', 'CLOSE')", name="ck_paper_order_action"),
        sa.CheckConstraint("status = 'EXECUTED'", name="ck_paper_order_status"),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"]),
        sa.ForeignKeyConstraint(["trade_id"], ["paper_trades.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_orders_account_id", "paper_orders", ["account_id"])
    op.create_index("ix_paper_orders_trade_id", "paper_orders", ["trade_id"])
    op.create_index("ix_paper_orders_user_id", "paper_orders", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_paper_orders_user_id", table_name="paper_orders")
    op.drop_index("ix_paper_orders_trade_id", table_name="paper_orders")
    op.drop_index("ix_paper_orders_account_id", table_name="paper_orders")
    op.drop_table("paper_orders")
    op.drop_index("ix_paper_trades_user_id", table_name="paper_trades")
    op.drop_index("ix_paper_trades_account_id", table_name="paper_trades")
    op.drop_table("paper_trades")
    op.drop_table("paper_accounts")
