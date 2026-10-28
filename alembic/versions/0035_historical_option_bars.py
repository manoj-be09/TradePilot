"""Add private historical NIFTY option candle import tables."""

import sqlalchemy as sa

from alembic import op

revision = "0035_historical_option_bars"
down_revision = "0034_feed_v3_recorder"
branch_labels = None
depends_on = None

_PRICE = sa.Numeric(14, 2)


def upgrade() -> None:
    op.create_table(
        "historical_option_bars",
        sa.Column("instrument_key", sa.String(length=64), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("bar_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("strike", _PRICE, nullable=False),
        sa.Column("option_type", sa.String(length=2), nullable=False),
        sa.Column("open", _PRICE, nullable=False),
        sa.Column("high", _PRICE, nullable=False),
        sa.Column("low", _PRICE, nullable=False),
        sa.Column("close", _PRICE, nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("open_interest", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("instrument_key", "interval", "bar_start"),
    )
    op.create_index(
        "ix_historical_option_bars_expiry",
        "historical_option_bars", ["expiry", "interval", "strike"],
    )
    op.create_table(
        "historical_import_contracts",
        sa.Column("instrument_key", sa.String(length=64), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("trading_symbol", sa.String(length=128), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("strike", _PRICE, nullable=False),
        sa.Column("option_type", sa.String(length=2), nullable=False),
        sa.Column("lot_size", sa.Integer(), nullable=False),
        sa.Column("requested_from", sa.Date(), nullable=False),
        sa.Column("requested_to", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("bar_count", sa.Integer(), nullable=False),
        sa.Column("first_bar", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_bar", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.String(length=300), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("instrument_key", "interval"),
    )
    op.create_index(
        "ix_historical_import_contracts_expiry",
        "historical_import_contracts", ["expiry", "interval"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_historical_import_contracts_expiry", table_name="historical_import_contracts"
    )
    op.drop_table("historical_import_contracts")
    op.drop_index("ix_historical_option_bars_expiry", table_name="historical_option_bars")
    op.drop_table("historical_option_bars")
