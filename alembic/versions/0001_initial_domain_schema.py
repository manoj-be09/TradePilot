"""Initial users, instrument master, and audit log."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial_domain_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=16), server_default="VIEWER", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("role IN ('ADMIN', 'TRADER', 'VIEWER')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "instruments",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("segment", sa.String(length=32), nullable=False),
        sa.Column("instrument_token", sa.String(length=128), nullable=False),
        sa.Column("trading_symbol", sa.String(length=128), nullable=False),
        sa.Column("underlying", sa.String(length=32), nullable=False),
        sa.Column("instrument_type", sa.String(length=32), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=True),
        sa.Column("strike", sa.Numeric(12, 2), nullable=True),
        sa.Column("option_type", sa.String(length=2), nullable=True),
        sa.Column("lot_size", sa.Integer(), nullable=False),
        sa.Column("tick_size", sa.Numeric(10, 4), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.CheckConstraint("lot_size > 0", name="ck_instruments_lot_size_positive"),
        sa.CheckConstraint("tick_size > 0", name="ck_instruments_tick_size_positive"),
        sa.CheckConstraint(
            "instrument_type IN ('INDEX', 'FUTURE', 'OPTION')", name="ck_instruments_type"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'EXPIRED')", name="ck_instruments_status"
        ),
        sa.CheckConstraint(
            "option_type IN ('CE', 'PE') OR option_type IS NULL", name="ck_instruments_option_type"
        ),
        sa.CheckConstraint(
            "(instrument_type = 'OPTION' AND expiry IS NOT NULL AND strike IS NOT NULL "
            "AND option_type IN ('CE', 'PE')) OR (instrument_type <> 'OPTION' "
            "AND expiry IS NULL AND strike IS NULL AND option_type IS NULL)",
            name="ck_instruments_option_fields",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_instruments_effective_range",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "exchange", "instrument_token", "effective_from", name="uq_instruments_token_version"
        ),
    )
    op.create_index("ix_instruments_trading_symbol", "instruments", ["trading_symbol"])
    op.create_index("ix_instruments_underlying", "instruments", ["underlying"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_index("ix_instruments_underlying", table_name="instruments")
    op.drop_index("ix_instruments_trading_symbol", table_name="instruments")
    op.drop_table("instruments")
    op.drop_table("users")
