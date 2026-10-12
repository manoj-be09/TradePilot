"""Store user-scoped, read-only market watchlist memberships."""

import sqlalchemy as sa

from alembic import op

revision = "0012_watchlist_items"
down_revision = "0011_multi_asset_instruments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("instrument_key", sa.String(160), nullable=False),
        sa.Column("trading_symbol", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256)),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("segment", sa.String(32), nullable=False),
        sa.Column("instrument_type", sa.String(24), nullable=False),
        sa.Column("expiry", sa.Date()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "provider", "instrument_key", name="uq_watchlist_user_key"),
    )
    op.create_index("ix_watchlist_items_user_id", "watchlist_items", ["user_id"])
    op.create_index("ix_watchlist_user_created", "watchlist_items", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_watchlist_user_created", table_name="watchlist_items")
    op.drop_index("ix_watchlist_items_user_id", table_name="watchlist_items")
    op.drop_table("watchlist_items")
