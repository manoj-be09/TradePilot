"""Retain unqualified point-in-time research opportunity events."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0032_research_opportunity_event"
down_revision = "0031_research_policy_set"
branch_labels = None
depends_on = None


def upgrade() -> None:
    payload = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table("research_opportunity_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recommendation_id", sa.Uuid(),
                  sa.ForeignKey("research_recommendations.id"), nullable=False),
        sa.Column("event_key", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", payload, nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("recommendation_id", "event_key",
                            name="uq_research_opportunity_event_key"),
        sa.CheckConstraint("status IN ('UNRESOLVED', 'SHADOW_NO_FILL', 'SHADOW_REJECTED')",
                           name="ck_research_opportunity_event_status"))
    op.create_index("ix_research_opportunity_events_user_id",
                    "research_opportunity_events", ["user_id"])
    op.create_index("ix_research_opportunity_events_recommendation_id",
                    "research_opportunity_events", ["recommendation_id"])


def downgrade() -> None:
    op.drop_index("ix_research_opportunity_events_recommendation_id",
                  table_name="research_opportunity_events")
    op.drop_index("ix_research_opportunity_events_user_id",
                  table_name="research_opportunity_events")
    op.drop_table("research_opportunity_events")
