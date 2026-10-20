"""Freeze a bounded research policy set for each parent study."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0031_research_policy_set"
down_revision = "0030_research_recommendation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    payload = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table("research_policy_sets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("study_id", sa.Uuid(), sa.ForeignKey("research_studies.id"), nullable=False),
        sa.Column("manifest", payload, nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("study_id", name="uq_research_policy_set_study"))
    op.create_index("ix_research_policy_sets_user_id", "research_policy_sets", ["user_id"])
    op.create_index("ix_research_policy_sets_study_id", "research_policy_sets", ["study_id"])


def downgrade() -> None:
    op.drop_index("ix_research_policy_sets_study_id", table_name="research_policy_sets")
    op.drop_index("ix_research_policy_sets_user_id", table_name="research_policy_sets")
    op.drop_table("research_policy_sets")
