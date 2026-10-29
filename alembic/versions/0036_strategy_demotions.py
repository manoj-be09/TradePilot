"""Add automatic paper strategy demotions and backtest drawdown baselines."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0036_strategy_demotions"
down_revision = "0035_paper_futures"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "strategy_drawdown_baselines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("strategy_key", sa.String(length=64), nullable=False),
        sa.Column("max_drawdown_per_lot", sa.Numeric(14, 2), nullable=False),
        sa.Column("trades", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("details", _JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("max_drawdown_per_lot >= 0", name="ck_drawdown_baseline_nonnegative"),
        sa.CheckConstraint("trades > 0", name="ck_drawdown_baseline_trades_positive"),
    )
    op.create_index(
        "ix_strategy_drawdown_baselines_key", "strategy_drawdown_baselines",
        ["strategy_key", "created_at"],
    )
    op.create_table(
        "strategy_demotions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("strategy_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("details", _JSON, nullable=False),
        sa.Column("demoted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reinstated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'REINSTATED')", name="ck_strategy_demotion_status"
        ),
    )
    op.create_index("ix_strategy_demotions_user_id", "strategy_demotions", ["user_id"])
    op.create_index(
        "ix_strategy_demotions_user_key", "strategy_demotions",
        ["user_id", "strategy_key", "demoted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_strategy_demotions_user_key", table_name="strategy_demotions")
    op.drop_index("ix_strategy_demotions_user_id", table_name="strategy_demotions")
    op.drop_table("strategy_demotions")
    op.drop_index("ix_strategy_drawdown_baselines_key", table_name="strategy_drawdown_baselines")
    op.drop_table("strategy_drawdown_baselines")
