"""Admit equity, ETF, and expiry-bearing future instruments."""

import sqlalchemy as sa

from alembic import op

revision = "0011_multi_asset_instruments"
down_revision = "0010_strategy_versions"
branch_labels = None
depends_on = None

_OLD_TYPE = "instrument_type IN ('INDEX', 'FUTURE', 'OPTION')"
_NEW_TYPE = "instrument_type IN ('INDEX', 'EQUITY', 'ETF', 'FUTURE', 'OPTION')"
_OLD_FIELDS = (
    "(instrument_type = 'OPTION' AND expiry IS NOT NULL AND strike IS NOT NULL "
    "AND option_type IN ('CE', 'PE')) OR (instrument_type <> 'OPTION' "
    "AND expiry IS NULL AND strike IS NULL AND option_type IS NULL)"
)
_NEW_FIELDS = (
    "(instrument_type = 'OPTION' AND expiry IS NOT NULL AND strike IS NOT NULL "
    "AND option_type IN ('CE', 'PE')) OR "
    "(instrument_type IN ('INDEX', 'EQUITY', 'ETF') AND expiry IS NULL "
    "AND strike IS NULL AND option_type IS NULL) OR "
    "(instrument_type = 'FUTURE' AND expiry IS NOT NULL AND strike IS NULL "
    "AND option_type IS NULL)"
)


def _replace_constraints(type_rule: str, field_rule: str) -> None:
    with op.batch_alter_table("instruments") as batch_op:
        batch_op.drop_constraint("ck_instruments_type", type_="check")
        batch_op.drop_constraint("ck_instruments_option_fields", type_="check")
        batch_op.create_check_constraint("ck_instruments_type", sa.text(type_rule))
        batch_op.create_check_constraint("ck_instruments_option_fields", sa.text(field_rule))


def upgrade() -> None:
    legacy_futures = op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM instruments "
            "WHERE instrument_type = 'FUTURE' AND expiry IS NULL"
        )
    )
    if legacy_futures:
        raise RuntimeError(
            "Assign expiry or retire legacy futures with missing expiry before this migration"
        )
    _replace_constraints(_NEW_TYPE, _NEW_FIELDS)


def downgrade() -> None:
    # Existing newer asset records cause the old checks to fail, preserving those rows.
    _replace_constraints(_OLD_TYPE, _OLD_FIELDS)
