"""Persist paper stop loss, target, and trailing stop settings."""

import sqlalchemy as sa

from alembic import op

revision = "0003_paper_exit_rules"
down_revision = "0002_paper_trading"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_trades", sa.Column("stop_loss_amount", sa.Numeric(14, 2)))
    op.add_column("paper_trades", sa.Column("target_profit_amount", sa.Numeric(14, 2)))
    op.add_column("paper_trades", sa.Column("trailing_stop_amount", sa.Numeric(14, 2)))
    op.add_column(
        "paper_trades",
        sa.Column("peak_unrealized_pnl", sa.Numeric(14, 2), server_default="0", nullable=False),
    )
    op.add_column("paper_trades", sa.Column("exit_reason", sa.String(length=24)))


def downgrade() -> None:
    op.drop_column("paper_trades", "exit_reason")
    op.drop_column("paper_trades", "peak_unrealized_pnl")
    op.drop_column("paper_trades", "trailing_stop_amount")
    op.drop_column("paper_trades", "target_profit_amount")
    op.drop_column("paper_trades", "stop_loss_amount")
