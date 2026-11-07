"""Add live releases, arming, and the live order ledger (LIVE_TRADING.md L5 locks)."""

import sqlalchemy as sa

from alembic import op

revision = "0043_live_ledger"
down_revision = "0042_live_halt"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True)


def _at(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "live_releases", _id(),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("strategy_key", sa.String(length=64), nullable=False),
        sa.Column("max_lots", sa.Integer(), nullable=False),
        sa.Column("max_trade_loss", sa.Numeric(14, 2), nullable=False),
        sa.Column("daily_loss_cap", sa.Numeric(14, 2), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("signature", sa.String(length=64), nullable=False),
        _at("created_at"), _at("revoked_at", nullable=True),
    )
    op.create_index("ix_live_releases_owner_user_id", "live_releases", ["owner_user_id"])
    op.create_table(
        "live_arm_events", _id(),
        sa.Column("release_id", sa.Uuid(), sa.ForeignKey("live_releases.id"), nullable=False),
        sa.Column("armed", sa.Boolean(), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=False),
        _at("occurred_at"),
    )
    op.create_index("ix_live_arm_events_release_id", "live_arm_events", ["release_id"])
    op.create_table(
        "live_orders", _id(),
        sa.Column("release_id", sa.Uuid(), sa.ForeignKey("live_releases.id"), nullable=False),
        sa.Column("tag", sa.String(length=40), nullable=False, unique=True),
        sa.Column("instrument_key", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("limit_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("filled_quantity", sa.Integer(), nullable=False),
        sa.Column("average_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("broker_order_id", sa.String(length=64), nullable=True),
        _at("created_at"), _at("updated_at"),
    )
    op.create_index("ix_live_orders_release_id", "live_orders", ["release_id"])
    op.create_table(
        "live_order_events", _id(),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("live_orders.id"), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("filled_quantity", sa.Integer(), nullable=False),
        sa.Column("broker_status", sa.String(length=64), nullable=True),
        _at("occurred_at"),
    )
    op.create_index("ix_live_order_events_order_id", "live_order_events", ["order_id"])


def downgrade() -> None:
    for table, column in (("live_order_events", "order_id"), ("live_orders", "release_id"),
                          ("live_arm_events", "release_id"), ("live_releases", "owner_user_id")):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_table(table)
