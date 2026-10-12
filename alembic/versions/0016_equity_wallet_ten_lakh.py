"""Raise separate equity paper wallet to ten lakh."""

from alembic import op

revision = "0016_equity_wallet_ten_lakh"
down_revision = "0015_strategy_library_expansion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve reservations/P&L from the old one-lakh equity paper wallet by adding
    # the difference only to accounts that have not already been moved to ten lakh.
    op.execute("UPDATE equity_accounts SET cash = cash + 900000 WHERE cash < 900000")


def downgrade() -> None:
    # Keep downgrade non-destructive; cash may include later paper-trading P&L.
    pass
