"""Add the strategy eligibility log and backfill it from existing demotions."""

from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0038_strategy_eligibility_events"
down_revision = "0037_historical_option_bars"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    events = op.create_table(
        "strategy_eligibility_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("strategy_key", sa.String(length=64), nullable=False),
        sa.Column(
            "demotion_id", sa.Uuid(), sa.ForeignKey("strategy_demotions.id"), nullable=False
        ),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.String(length=256), nullable=False),
        sa.Column("details", _JSON, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('DEMOTED', 'PROBATION_STARTED', 'PROBATION_FAILED', 'PROMOTED', "
            "'REINSTATED')",
            name="ck_strategy_eligibility_event_type",
        ),
    )
    op.create_index(
        "ix_strategy_eligibility_events_user_time", "strategy_eligibility_events",
        ["user_id", "occurred_at"],
    )
    op.create_index(
        "ix_strategy_eligibility_events_demotion", "strategy_eligibility_events",
        ["demotion_id", "occurred_at"],
    )
    demotions = sa.table(
        "strategy_demotions",
        sa.column("id", sa.Uuid()), sa.column("user_id", sa.Uuid()),
        sa.column("strategy_key", sa.String()), sa.column("reason", sa.String()),
        sa.column("details", _JSON), sa.column("demoted_at", sa.DateTime(timezone=True)),
        sa.column("reinstated_at", sa.DateTime(timezone=True)),
    )
    rows = []
    for row in op.get_bind().execute(sa.select(demotions)).mappings():
        common = {"user_id": row["user_id"], "strategy_key": row["strategy_key"],
                  "demotion_id": row["id"], "details": {}}
        rows.append({
            **common, "id": uuid4(), "event_type": "DEMOTED", "reason": row["reason"],
            "summary": str(row["details"].get("summary") or row["reason"])[:256],
            "occurred_at": row["demoted_at"],
        })
        if row["reinstated_at"] is not None:
            rows.append({
                **common, "id": uuid4(), "event_type": "REINSTATED", "reason": "OWNER_REVIEW",
                "summary": "Reinstated by the owner after review",
                "occurred_at": row["reinstated_at"],
            })
    if rows:
        op.bulk_insert(events, rows)


def downgrade() -> None:
    op.drop_index(
        "ix_strategy_eligibility_events_demotion", table_name="strategy_eligibility_events"
    )
    op.drop_index(
        "ix_strategy_eligibility_events_user_time", table_name="strategy_eligibility_events"
    )
    op.drop_table("strategy_eligibility_events")
