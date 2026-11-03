"""Add the research rule engine: frozen rule versions, their shadow trades and decisions."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0040_research_rule_engine"
down_revision = "0039_futures_deployments"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "research_rule_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rule_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("params", _JSON, nullable=False),
        sa.Column("forward_from", sa.Date(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("stage_since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_check", _JSON, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rule_key", "version", name="uq_research_rule_version"),
        sa.CheckConstraint(
            "stage IN ('SHADOW', 'PAPER_APPROVED', 'SUPERSEDED', 'DEMOTED', 'RETIRED')",
            name="ck_research_rule_stage",
        ),
    )
    op.create_table(
        "research_shadow_trades",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rule_version_id", sa.Uuid(), sa.ForeignKey("research_rule_versions.id"),
                  nullable=False),
        sa.Column("session", sa.Date(), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("gross_pnl", sa.Numeric(14, 2), nullable=False),
        sa.Column("charges", sa.Numeric(14, 2), nullable=False),
        sa.Column("net_pnl", sa.Numeric(14, 2), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rule_version_id", "session", name="uq_research_shadow_trade_session"),
    )
    op.create_table(
        "research_rule_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rule_version_id", sa.Uuid(), sa.ForeignKey("research_rule_versions.id"),
                  nullable=False),
        sa.Column("rule_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.String(length=256), nullable=False),
        sa.Column("details", _JSON, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('REGISTERED', 'PROMOTED', 'SUPERSEDED', 'DEMOTED', 'ROLLED_BACK', "
            "'RETIRED')",
            name="ck_research_rule_event_type",
        ),
    )
    op.create_index("ix_research_rule_events_time", "research_rule_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_research_rule_events_time", table_name="research_rule_events")
    op.drop_table("research_rule_events")
    op.drop_table("research_shadow_trades")
    op.drop_table("research_rule_versions")
