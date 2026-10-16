"""Store prospective research-only recommendation and abstention snapshots."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0030_research_recommendation"
down_revision = "0029_research_study"
branch_labels = None
depends_on = None


def upgrade() -> None:
    payload = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table("research_recommendations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("study_id", sa.Uuid(), sa.ForeignKey("research_studies.id"), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(48), nullable=False),
        sa.Column("snapshot", payload, nullable=False),
        sa.Column("snapshot_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "snapshot_digest",
                            name="uq_research_recommendation_snapshot"),
        sa.CheckConstraint(
            "decision IN ('ABSTAIN', 'REVIEW_CANDIDATE')",
            name="ck_research_recommendation_decision"))
    op.create_index("ix_research_recommendations_user_id", "research_recommendations", ["user_id"])
    op.create_index(
        "ix_research_recommendations_study_id", "research_recommendations", ["study_id"])


def downgrade() -> None:
    op.drop_index("ix_research_recommendations_study_id", table_name="research_recommendations")
    op.drop_index("ix_research_recommendations_user_id", table_name="research_recommendations")
    op.drop_table("research_recommendations")
