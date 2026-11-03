"""Add the v2 learning loop: forward hypotheses with an error budget, observations, decisions."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0041_learning_v2"
down_revision = "0040_research_rule_engine"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
# Copied, not imported: a migration must not change when the models do.
LEARNING_STAGES = ("UNDERPOWERED", "SHADOW", "PROBATION", "APPROVED", "SUPERSEDED", "DEMOTED",
                   "RETIRED", "BASELINE")
LEARNING_EVENTS = ("REGISTERED", "REFUSED", "TO_PROBATION", "APPROVED", "SUPERSEDED",
                   "DEMOTED", "ROLLED_BACK", "RETIRED")


def _within(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ('" + "', '".join(values) + "')"


def upgrade() -> None:
    op.create_table(
        "learning_hypotheses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(length=96), nullable=False),
        sa.Column("family", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("params", _JSON, nullable=False),
        sa.Column("forward_from", sa.Date(), nullable=False),
        sa.Column("origin", sa.String(length=8), nullable=False),
        sa.Column("override", sa.Boolean(), nullable=False),
        sa.Column("baseline_key", sa.String(length=96), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("preregistration", _JSON, nullable=False),
        sa.Column("budget_sequence", sa.Integer(), nullable=True),
        sa.Column("alpha", sa.Float(), nullable=True),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("stage_since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_check", _JSON, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key", name="uq_learning_hypothesis_key"),
        sa.UniqueConstraint("budget_sequence", name="uq_learning_budget_sequence"),
        sa.CheckConstraint(_within("stage", LEARNING_STAGES), name="ck_learning_stage"),
        sa.CheckConstraint("origin IN ('HUMAN', 'GRID', 'LLM')", name="ck_learning_origin"),
    )
    op.create_table(
        "learning_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("hypothesis_id", sa.Uuid(), sa.ForeignKey("learning_hypotheses.id"),
                  nullable=False),
        sa.Column("session", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("net_pnl", sa.Numeric(14, 2), nullable=False),
        sa.Column("max_loss", sa.Numeric(14, 2), nullable=False),
        sa.Column("max_gain", sa.Numeric(14, 2), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("hypothesis_id", "session", "source", name="uq_learning_observation"),
        sa.CheckConstraint("source IN ('SHADOW', 'PAPER')", name="ck_learning_source"),
    )
    op.create_index("ix_learning_observations_hypothesis_id", "learning_observations",
                    ["hypothesis_id"])
    op.create_table(
        "learning_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("hypothesis_id", sa.Uuid(), sa.ForeignKey("learning_hypotheses.id"),
                  nullable=False),
        sa.Column("key", sa.String(length=96), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.String(length=256), nullable=False),
        sa.Column("details", _JSON, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_within("event_type", LEARNING_EVENTS), name="ck_learning_event_type"),
    )
    op.create_index("ix_learning_events_time", "learning_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_learning_events_time", table_name="learning_events")
    op.drop_table("learning_events")
    op.drop_index("ix_learning_observations_hypothesis_id", table_name="learning_observations")
    op.drop_table("learning_observations")
    op.drop_table("learning_hypotheses")
