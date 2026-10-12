"""Persist each equity paper wallet's starting capital."""

import sqlalchemy as sa

from alembic import op

revision = "0022_equity_initial_capital"
down_revision = "0021_equity_policy_trials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Earlier accounts were moved to the ten-lakh paper baseline in revision 0016.
    # Do not alter their current cash, reservations, or saved P&L.
    op.add_column(
        "equity_accounts",
        sa.Column(
            "initial_cash", sa.Numeric(14, 2), nullable=False,
            server_default=sa.text("1000000.00"),
        ),
    )


def downgrade() -> None:
    op.drop_column("equity_accounts", "initial_cash")
