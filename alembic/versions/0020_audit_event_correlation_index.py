"""Index account-scoped paper decision and quote-observation history."""

from alembic import op

revision = "0020_audit_event_corr_idx"
down_revision = "0019_equity_validation_dataset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_events_owner_correlation_type_created",
        "audit_events",
        ["actor_user_id", "correlation_id", "event_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_owner_correlation_type_created", "audit_events")
