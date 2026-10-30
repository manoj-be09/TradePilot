"""Let armed paper deployments hold NIFTY futures templates and link their fills."""

import sqlalchemy as sa

from alembic import op

revision = "0038_futures_deployments"
down_revision = "0037_historical_option_bars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("paper_deployments") as batch:
        batch.add_column(sa.Column(
            "instrument_kind", sa.String(length=12), nullable=False, server_default="OPTION",
        ))
        batch.create_check_constraint(
            "ck_paper_deployment_instrument_kind", "instrument_kind IN ('OPTION', 'FUTURES')",
        )
    with op.batch_alter_table("paper_futures_trades") as batch:
        batch.add_column(sa.Column("strategy_key", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("deployment_id", sa.Uuid(), nullable=True))
        batch.create_index("ix_paper_futures_trades_strategy_key", ["strategy_key"])
        batch.create_unique_constraint("uq_paper_futures_deployment", ["deployment_id"])
        batch.create_foreign_key(
            "fk_paper_futures_deployment", "paper_deployments", ["deployment_id"], ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("paper_futures_trades") as batch:
        batch.drop_constraint("fk_paper_futures_deployment", type_="foreignkey")
        batch.drop_constraint("uq_paper_futures_deployment", type_="unique")
        batch.drop_index("ix_paper_futures_trades_strategy_key")
        batch.drop_column("deployment_id")
        batch.drop_column("strategy_key")
    with op.batch_alter_table("paper_deployments") as batch:
        batch.drop_constraint("ck_paper_deployment_instrument_kind", type_="check")
        batch.drop_column("instrument_kind")
