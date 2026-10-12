"""Record future equity paper-wallet movements without inventing legacy fills."""

import sqlalchemy as sa

from alembic import op

revision = "0023_equity_cash_journal"
down_revision = "0022_equity_initial_capital"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equity_cash_events",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event_key", sa.String(128), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("delta", sa.Numeric(14, 2), nullable=False),
        sa.Column("cash_after", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "event_key", name="uq_equity_cash_event_key"),
    )
    op.create_index(
        "ix_equity_cash_user_id", "equity_cash_events", ["user_id", "id"]
    )
    # Existing balances become labeled checkpoints. Past orders are not fabricated.
    op.execute(sa.text(
        "INSERT INTO equity_cash_events "
        "(user_id, event_key, deployment_id, kind, delta, cash_after) "
        "SELECT user_id, 'opening', NULL, 'LEGACY_CHECKPOINT', 0, cash "
        "FROM equity_accounts"
    ))


def downgrade() -> None:
    op.drop_index("ix_equity_cash_user_id", table_name="equity_cash_events")
    op.drop_table("equity_cash_events")
