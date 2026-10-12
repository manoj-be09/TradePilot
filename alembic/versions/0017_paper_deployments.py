"""Durable waiting deployments for autonomous options paper entry."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0017_paper_deployments"
down_revision = "0016_equity_wallet_ten_lakh"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_deployments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("command_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request_data", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("detail", sa.String(256), nullable=False),
        sa.Column("trade_id", sa.Uuid(), sa.ForeignKey("paper_trades.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "command_key", name="uq_paper_deployment_command"),
    )
    op.create_index("ix_paper_deployment_status", "paper_deployments", ["status", "created_at"])
    op.create_index("ix_paper_deployments_user_id", "paper_deployments", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_paper_deployments_user_id", table_name="paper_deployments")
    op.drop_index("ix_paper_deployment_status", table_name="paper_deployments")
    op.drop_table("paper_deployments")
