"""Freeze account-scoped chronological equity validation evidence."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0018_equity_validation_studies"
down_revision = "0017_paper_deployments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equity_validation_studies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("instrument_key", sa.String(160), nullable=False),
        sa.Column("symbol", sa.String(128), nullable=False),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("allocation", sa.Numeric(14, 2), nullable=False),
        sa.Column("risk_budget", sa.Numeric(14, 2), nullable=False),
        sa.Column("dataset_digest", sa.String(64), nullable=False),
        sa.Column("report_digest", sa.String(64), nullable=False),
        sa.Column("report", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "provider", "instrument_key", name="uq_equity_validation_scope"
        ),
    )
    op.create_index(
        "ix_equity_validation_studies_user_id", "equity_validation_studies", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_equity_validation_studies_user_id", table_name="equity_validation_studies")
    op.drop_table("equity_validation_studies")
