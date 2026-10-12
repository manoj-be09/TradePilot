"""Freeze predeclared equity exit-policy comparisons."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0021_equity_policy_trials"
down_revision = "0020_audit_event_corr_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equity_policy_trials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "study_id", sa.Uuid(),
            sa.ForeignKey("equity_validation_studies.id"), nullable=False,
        ),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("registry_version", sa.String(40), nullable=False),
        sa.Column("trial_manifest", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "study_id", "strategy", "registry_version", name="uq_equity_policy_trial"
        ),
    )
    op.create_index("ix_equity_policy_trials_user_id", "equity_policy_trials", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_equity_policy_trials_user_id", table_name="equity_policy_trials")
    op.drop_table("equity_policy_trials")
