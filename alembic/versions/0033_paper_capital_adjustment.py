"""Add an auditable simulated F&O wallet funding journal."""

import sqlalchemy as sa

from alembic import op

revision = "0033_paper_capital_adjustment"
down_revision = "0032_research_opportunity_event"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_capital_adjustments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("paper_accounts.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("cash_before", sa.Numeric(14, 2), nullable=False),
        sa.Column("cash_delta", sa.Numeric(14, 2), nullable=False),
        sa.Column("cash_after", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "idempotency_key", name="uq_paper_capital_adjustment_key"
        ),
        sa.CheckConstraint("cash_delta > 0", name="ck_paper_capital_adjustment_positive"),
        sa.CheckConstraint(
            "cash_after = cash_before + cash_delta",
            name="ck_paper_capital_adjustment_balance",
        ),
    )
    op.create_index(
        "ix_paper_capital_adjustments_account_id",
        "paper_capital_adjustments", ["account_id"]
    )
    op.create_index(
        "ix_paper_capital_adjustments_user_id",
        "paper_capital_adjustments", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_paper_capital_adjustments_user_id",
        table_name="paper_capital_adjustments"
    )
    op.drop_index(
        "ix_paper_capital_adjustments_account_id",
        table_name="paper_capital_adjustments"
    )
    op.drop_table("paper_capital_adjustments")
