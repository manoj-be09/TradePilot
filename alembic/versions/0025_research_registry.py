"""Account-scoped immutable research definitions and predeclared attempts."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0025_research_registry"
down_revision = "0024_equity_study_generations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    payload = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table("research_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False),
        sa.Column("definition", payload, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "definition_digest",
                            name="uq_research_candidate_definition"))
    op.create_index("ix_research_candidates_user_id", "research_candidates", ["user_id"])
    op.create_table("research_trials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), sa.ForeignKey("research_candidates.id"),
                  nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("manifest", payload, nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("result", payload, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "candidate_id", "manifest_digest",
                            name="uq_research_trial_manifest"))
    op.create_index("ix_research_trials_user_id", "research_trials", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_research_trials_user_id", table_name="research_trials")
    op.drop_table("research_trials")
    op.drop_index("ix_research_candidates_user_id", table_name="research_candidates")
    op.drop_table("research_candidates")
