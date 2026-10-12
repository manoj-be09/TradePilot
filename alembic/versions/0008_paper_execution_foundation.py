"""Durable entry deduplication and fenced paper-worker ownership."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0008_paper_execution_foundation"
down_revision = "0007_paper_strategy_presets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_entry_commands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("trade_id", sa.Uuid(), sa.ForeignKey("paper_trades.id"), nullable=False),
        sa.Column("response_data", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_paper_entry_command"),
    )
    op.create_table(
        "worker_leases",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("detail", sa.String(512), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worker_leases")
    op.drop_table("paper_entry_commands")
