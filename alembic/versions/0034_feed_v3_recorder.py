"""Add private Feed V3 recorder tables."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0034_feed_v3_recorder"
down_revision = "0033_paper_capital_adjustment"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_PRICE = sa.Numeric(14, 2)
_RATIO = sa.Numeric(18, 8)


def upgrade() -> None:
    op.create_table(
        "market_snapshots_1m",
        sa.Column("instrument_key", sa.String(length=64), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trading_day", sa.Date(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_trade_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ltp_open", _PRICE, nullable=True),
        sa.Column("ltp_high", _PRICE, nullable=True),
        sa.Column("ltp_low", _PRICE, nullable=True),
        sa.Column("ltp_close", _PRICE, nullable=True),
        sa.Column("last_trade_qty", sa.BigInteger(), nullable=True),
        sa.Column("prev_close", _PRICE, nullable=True),
        sa.Column("bid", _PRICE, nullable=True),
        sa.Column("ask", _PRICE, nullable=True),
        sa.Column("bid_qty", sa.BigInteger(), nullable=True),
        sa.Column("ask_qty", sa.BigInteger(), nullable=True),
        sa.Column("depth", _JSON, nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("open_interest", sa.Numeric(18, 2), nullable=True),
        sa.Column("iv", _RATIO, nullable=True),
        sa.Column("delta", _RATIO, nullable=True),
        sa.Column("gamma", _RATIO, nullable=True),
        sa.Column("theta", _RATIO, nullable=True),
        sa.Column("vega", _RATIO, nullable=True),
        sa.Column("rho", _RATIO, nullable=True),
        sa.Column("avg_traded_price", _PRICE, nullable=True),
        sa.Column("total_buy_qty", sa.Numeric(18, 2), nullable=True),
        sa.Column("total_sell_qty", sa.Numeric(18, 2), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("instrument_key", "bucket_start"),
    )
    op.create_index(
        "ix_market_snapshots_1m_day_key",
        "market_snapshots_1m", ["trading_day", "instrument_key"],
    )
    op.create_table(
        "recorded_instruments",
        sa.Column("trading_day", sa.Date(), nullable=False),
        sa.Column("instrument_key", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("trading_symbol", sa.String(length=128), nullable=True),
        sa.Column("expiry", sa.Date(), nullable=True),
        sa.Column("strike", sa.Numeric(14, 2), nullable=True),
        sa.Column("option_type", sa.String(length=2), nullable=True),
        sa.Column("lot_size", sa.Integer(), nullable=True),
        sa.Column("tick_size", sa.Numeric(10, 4), nullable=True),
        sa.Column("first_subscribed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("trading_day", "instrument_key"),
    )
    op.create_table(
        "recorder_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trading_day", sa.Date(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("detail", _JSON, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_recorder_events_day", "recorder_events", ["trading_day", "occurred_at"]
    )
    op.create_table(
        "recorder_day_summaries",
        sa.Column("trading_day", sa.Date(), nullable=False),
        sa.Column("session_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("instruments", sa.Integer(), nullable=False),
        sa.Column("session_minutes", sa.Integer(), nullable=False),
        sa.Column("connected_minutes", sa.Integer(), nullable=False),
        sa.Column("index_minutes", sa.Integer(), nullable=False),
        sa.Column("snapshot_rows", sa.Integer(), nullable=False),
        sa.Column("frames", sa.BigInteger(), nullable=False),
        sa.Column("raw_bytes", sa.BigInteger(), nullable=False),
        sa.Column("disconnects", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("trading_day"),
    )


def downgrade() -> None:
    op.drop_table("recorder_day_summaries")
    op.drop_index("ix_recorder_events_day", table_name="recorder_events")
    op.drop_table("recorder_events")
    op.drop_table("recorded_instruments")
    op.drop_index("ix_market_snapshots_1m_day_key", table_name="market_snapshots_1m")
    op.drop_table("market_snapshots_1m")
