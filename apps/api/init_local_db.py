"""Create the ORM schema for a local SQLite development database only."""

from pathlib import Path
from runpy import run_path

from sqlalchemy import Engine, insert, inspect, select, text

from apps.api.settings import get_settings
from packages.database import (
    futures_models,  # noqa: F401 - register futures paper tables
    models,  # noqa: F401 - register models in metadata
    recorder_models,  # noqa: F401 - register recorder tables
)
from packages.database.base import Base
from packages.database.models import StrategyCatalogEntry
from packages.database.session import build_session_factory


def main() -> None:
    database_url = get_settings().database_url
    if not database_url.startswith("sqlite:"):
        raise SystemExit(
            "This helper only initializes SQLite. Use Alembic for PostgreSQL databases."
        )

    factory = build_session_factory(database_url)
    engine = factory.kw["bind"]
    if not isinstance(engine, Engine):
        raise SystemExit("Could not create the local database engine")
    Base.metadata.create_all(engine)
    # create_all does not alter existing local tables; keep the user's SQLite DB usable
    # when new development columns are introduced without deleting existing records.
    paper_trade_columns = {column["name"] for column in inspect(engine).get_columns("paper_trades")}
    local_upgrades = {
        "stop_loss_amount": ("ALTER TABLE paper_trades ADD COLUMN stop_loss_amount NUMERIC(14, 2)"),
        "target_profit_amount": (
            "ALTER TABLE paper_trades ADD COLUMN target_profit_amount NUMERIC(14, 2)"
        ),
        "trailing_stop_amount": (
            "ALTER TABLE paper_trades ADD COLUMN trailing_stop_amount NUMERIC(14, 2)"
        ),
        "peak_unrealized_pnl": (
            "ALTER TABLE paper_trades ADD COLUMN peak_unrealized_pnl "
            "NUMERIC(14, 2) NOT NULL DEFAULT 0"
        ),
        "exit_reason": "ALTER TABLE paper_trades ADD COLUMN exit_reason VARCHAR(24)",
        "entry_rule_snapshot": (
            "ALTER TABLE paper_trades ADD COLUMN entry_rule_snapshot JSON NOT NULL DEFAULT '{}'"
        ),
    }
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_audit_events_owner_correlation_type_created "
            "ON audit_events (actor_user_id, correlation_id, event_type, created_at)"
        ))
        for column, definition in local_upgrades.items():
            if column not in paper_trade_columns:
                connection.execute(text(definition))
        equity_account_columns = {
            column["name"] for column in inspect(connection).get_columns("equity_accounts")
        }
        if "initial_cash" not in equity_account_columns:
            connection.execute(text(
                "ALTER TABLE equity_accounts ADD COLUMN initial_cash "
                "NUMERIC(14, 2) NOT NULL DEFAULT 1000000.00"
            ))
        # Checkpoint legacy balances once; never imply their earlier cash flows were observed.
        connection.execute(text(
            "INSERT INTO equity_cash_events "
            "(user_id, event_key, deployment_id, kind, delta, cash_after) "
            "SELECT user_id, 'opening', NULL, 'LEGACY_CHECKPOINT', 0, cash "
            "FROM equity_accounts AS account WHERE NOT EXISTS "
            "(SELECT 1 FROM equity_cash_events AS event "
            "WHERE event.user_id = account.user_id AND event.event_key = 'opening')"
        ))
        validation_columns = {
            column["name"] for column in inspect(connection).get_columns(
                "equity_validation_studies"
            )
        }
        if "dataset_manifest" not in validation_columns:
            connection.execute(text(
                "ALTER TABLE equity_validation_studies ADD COLUMN dataset_manifest JSON"
            ))
        # Local create_all bypasses Alembic; seed the same immutable v1 catalog.
        seed_path = (
            Path(__file__).resolve().parents[2] / "alembic/versions/0014_strategy_catalog.py"
        )
        seed_rows = run_path(str(seed_path))["ROWS"]
        expansion = seed_path.with_name("0015_strategy_library_expansion.py")
        seed_rows += run_path(str(expansion))["ROWS"]
        existing = set(connection.scalars(select(StrategyCatalogEntry.key)))
        missing = [row for row in seed_rows if row["key"] not in existing]
        if missing:
            connection.execute(insert(StrategyCatalogEntry), missing)
    print("Local SQLite schema is ready.")


if __name__ == "__main__":
    main()
