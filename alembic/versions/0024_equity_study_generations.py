"""Preserve frozen studies while allowing explicit successor generations."""

import sqlalchemy as sa

from alembic import op

revision = "0024_equity_study_generations"
down_revision = "0023_equity_cash_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("equity_validation_studies") as batch:
        batch.add_column(sa.Column("generation", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("parent_study_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_equity_validation_parent", "equity_validation_studies", ["parent_study_id"], ["id"]
        )
        batch.drop_constraint("uq_equity_validation_scope", type_="unique")
        batch.create_unique_constraint(
            "uq_equity_validation_generation",
            ["user_id", "provider", "instrument_key", "generation"],
        )
        batch.create_unique_constraint("uq_equity_validation_parent", ["parent_study_id"])


def downgrade() -> None:
    count = op.get_bind().scalar(sa.text(
        "SELECT count(*) FROM equity_validation_studies "
        "WHERE generation <> 1 OR parent_study_id IS NOT NULL"
    ))
    if count:
        raise RuntimeError("Cannot downgrade study generations while successor evidence exists")
    with op.batch_alter_table("equity_validation_studies") as batch:
        batch.drop_constraint("uq_equity_validation_parent", type_="unique")
        batch.drop_constraint("uq_equity_validation_generation", type_="unique")
        batch.drop_constraint("fk_equity_validation_parent", type_="foreignkey")
        batch.create_unique_constraint(
            "uq_equity_validation_scope", ["user_id", "provider", "instrument_key"]
        )
        batch.drop_column("parent_study_id")
        batch.drop_column("generation")
