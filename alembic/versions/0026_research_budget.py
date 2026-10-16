"""Add durable research-family budgets without changing legacy trials."""
import sqlalchemy as sa

from alembic import op

revision = "0026_research_budget"
down_revision = "0025_research_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("research_experiments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("family", sa.String(100), nullable=False),
        sa.Column("budget", sa.Integer(), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "family", name="uq_research_experiment_family"),
        sa.CheckConstraint("budget >= 1 AND budget <= 10000", name="ck_research_budget"))
    op.create_index("ix_research_experiments_user_id", "research_experiments", ["user_id"])
    op.create_table("research_budget_slots",
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("research_experiments.id"),
                  primary_key=True),
        sa.Column("slot", sa.Integer(), primary_key=True),
        sa.Column("trial_id", sa.Uuid(), sa.ForeignKey("research_trials.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("trial_id", name="uq_research_budget_trial"),
        sa.CheckConstraint("slot >= 1 AND slot <= 10000", name="ck_research_slot"))


def downgrade() -> None:
    op.drop_table("research_budget_slots")
    op.drop_index("ix_research_experiments_user_id", table_name="research_experiments")
    op.drop_table("research_experiments")
