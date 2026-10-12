"""Preserve the exact inputs used by a frozen equity validation study."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0019_equity_validation_dataset"
down_revision = "0018_equity_validation_studies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing studies cannot be backfilled from a mutable market-data source.
    op.add_column(
        "equity_validation_studies",
        sa.Column(
            "dataset_manifest", sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("equity_validation_studies", "dataset_manifest")
