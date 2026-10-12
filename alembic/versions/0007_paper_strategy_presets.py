"""Persist reusable, account-scoped paper strategy presets."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_paper_strategy_presets"
down_revision = "0006_backtest_run_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_strategy_presets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("strategy_key", sa.String(length=64), nullable=False),
        sa.Column(
            "entry_conditions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_paper_strategy_presets_user_name"),
    )
    op.create_index("ix_paper_strategy_presets_user_id", "paper_strategy_presets", ["user_id"])
    op.create_index(
        "ix_paper_strategy_presets_user_created",
        "paper_strategy_presets",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_paper_strategy_presets_user_created", table_name="paper_strategy_presets")
    op.drop_index("ix_paper_strategy_presets_user_id", table_name="paper_strategy_presets")
    op.drop_table("paper_strategy_presets")
