"""Persist immutable NIFTY parent study contracts and budgets."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0029_research_study"
down_revision = "0028_research_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    payload = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table("research_studies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("study_key", sa.String(100), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("manifest", payload, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "study_key", name="uq_research_study_key"))
    op.create_index("ix_research_studies_user_id", "research_studies", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_research_studies_user_id", table_name="research_studies")
    op.drop_table("research_studies")
