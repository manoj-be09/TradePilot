"""Store immutable artifact-linked readiness evidence for research trials."""
import sqlalchemy as sa

from alembic import op

revision = "0028_research_evidence"
down_revision = "0027_research_evaluation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("research_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("trial_id", sa.Uuid(), sa.ForeignKey("research_trials.id"), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("detail", sa.String(1000), nullable=False),
        sa.Column("artifact_ref", sa.String(500), nullable=False),
        sa.Column("artifact_digest", sa.String(64), nullable=True),
        sa.Column("artifact_version", sa.String(128), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("allowed_use", sa.String(32), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("trial_id", "key", name="uq_research_evidence_trial_key"),
        sa.CheckConstraint(
            "status IN ('PASS', 'FAIL', 'UNKNOWN', 'NOT_APPLICABLE')",
            name="ck_research_evidence_status"),
        sa.CheckConstraint(
            "allowed_use IN ('INSPECT_RESEARCH_RECORDS', 'RESEARCH_ONLY', 'QUALIFIED_PAPER')",
            name="ck_research_evidence_allowed_use"))
    op.create_index("ix_research_evidence_user_id", "research_evidence", ["user_id"])
    op.create_index("ix_research_evidence_trial_id", "research_evidence", ["trial_id"])


def downgrade() -> None:
    op.drop_index("ix_research_evidence_trial_id", table_name="research_evidence")
    op.drop_index("ix_research_evidence_user_id", table_name="research_evidence")
    op.drop_table("research_evidence")
