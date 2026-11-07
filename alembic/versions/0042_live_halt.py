"""Add the append-only live kill switch (LIVE_TRADING.md section 5)."""

import sqlalchemy as sa

from alembic import op

revision = "0042_live_halt"
down_revision = "0041_learning_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_halt_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("halted", sa.Boolean(), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_live_halt_events_time", "live_halt_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_live_halt_events_time", table_name="live_halt_events")
    op.drop_table("live_halt_events")
