"""Persist no-code market filters used when opening paper trades."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_paper_entry_rules"
down_revision = "0003_paper_exit_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paper_trades",
        sa.Column(
            "entry_rule_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("paper_trades", "entry_rule_snapshot")
