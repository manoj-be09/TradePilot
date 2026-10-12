"""Store immutable, user-scoped strategy definition versions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0010_strategy_versions"
down_revision = "0009_auth_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("definition", JSONB(), nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "user_id", "name", "version_number", name="uq_strategy_version_number"
        ),
    )
    op.create_index("ix_strategy_versions_user_id", "strategy_versions", ["user_id"])
    op.create_index(
        "ix_strategy_versions_user_name", "strategy_versions", ["user_id", "name"]
    )


def downgrade() -> None:
    op.drop_index("ix_strategy_versions_user_name", table_name="strategy_versions")
    op.drop_index("ix_strategy_versions_user_id", table_name="strategy_versions")
    op.drop_table("strategy_versions")
