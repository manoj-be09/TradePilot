"""Independent equity simulation balance and durable versioned deployments."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0013_equity_deployments"
down_revision = "0012_watchlist_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equity_accounts",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("cash", sa.Numeric(14, 2), nullable=False),
    )
    op.create_table(
        "equity_deployments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("command_key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("instrument_key", sa.String(160), nullable=False),
        sa.Column("symbol", sa.String(128), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("budget", sa.Numeric(14, 2), nullable=False),
        sa.Column("risk_budget", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "state",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "command_key", name="uq_equity_command"),
    )
    op.create_index("ix_equity_user_status", "equity_deployments", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_equity_user_status", table_name="equity_deployments")
    op.drop_table("equity_deployments")
    op.drop_table("equity_accounts")
