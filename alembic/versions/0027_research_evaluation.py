"""Keep typed replay bindings and engine evidence separate from operator results."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0027_research_evaluation"
down_revision = "0026_research_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    payload = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table("research_evaluations",
        sa.Column("trial_id", sa.Uuid(), sa.ForeignKey("research_trials.id"), primary_key=True),
        sa.Column("adapter", payload, nullable=False),
        sa.Column("adapter_digest", sa.String(64), nullable=False),
        sa.Column("result", payload, nullable=True),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_table("research_evaluations")
