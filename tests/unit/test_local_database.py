import importlib.util
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from apps.api.init_local_db import main
from apps.api.settings import get_settings
from packages.database.base import Base


def test_local_database_initializer_creates_sqlite_schema(tmp_path, monkeypatch):
    database_path = tmp_path / "local.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    main()
    assert database_path.exists()
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert inspect(engine).has_table("user_notifications")
    assert "ix_audit_events_owner_correlation_type_created" in {
        item["name"] for item in inspect(engine).get_indexes("audit_events")
    }
    engine.dispose()
    get_settings.cache_clear()


def test_local_initializer_preserves_equity_cash_when_funding_column_is_added(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "equity-wallet.sqlite3"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE equity_accounts (user_id TEXT PRIMARY KEY, cash NUMERIC(14, 2))"
        ))
        connection.execute(text(
            "INSERT INTO equity_accounts (user_id, cash) VALUES ('legacy', 987654.32)"
        ))
    engine.dispose()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    main()
    main()
    upgraded = create_engine(f"sqlite:///{database_path.as_posix()}")
    with upgraded.connect() as connection:
        cash, initial = connection.execute(text(
            "SELECT cash, initial_cash FROM equity_accounts WHERE user_id = 'legacy'"
        )).one()
        checkpoint = connection.execute(text(
            "SELECT kind, delta, cash_after FROM equity_cash_events WHERE user_id = 'legacy'"
        )).all()
    assert str(cash) == "987654.32"
    assert str(initial) == "1000000"
    assert checkpoint == [("LEGACY_CHECKPOINT", 0, 987654.32)]
    upgraded.dispose()
    get_settings.cache_clear()


def test_alembic_equity_funding_upgrade_preserves_existing_cash(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "equity_funding_migration", Path("alembic/versions/0022_equity_initial_capital.py")
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{(tmp_path / 'funding-migration.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE equity_accounts (user_id TEXT PRIMARY KEY, cash NUMERIC(14, 2))"
        ))
        connection.execute(text(
            "INSERT INTO equity_accounts (user_id, cash) VALUES ('legacy', 987654.32)"
        ))
        original_op = migration.op
        migration.op = Operations(MigrationContext.configure(connection))
        try:
            migration.upgrade()
            cash, initial = connection.execute(text(
                "SELECT cash, initial_cash FROM equity_accounts WHERE user_id = 'legacy'"
            )).one()
            assert str(cash) == "987654.32"
            assert str(initial) == "1000000"
        finally:
            migration.op = original_op
    engine.dispose()


def test_alembic_equity_cash_journal_upgrade_preserves_wallet(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "equity_cash_journal_migration",
        Path("alembic/versions/0023_equity_cash_journal.py"),
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{(tmp_path / 'journal-migration.db').as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id, email, password_hash) "
            "VALUES ('legacy', 'legacy@example.test', 'unused')"
        ))
        connection.execute(text(
            "INSERT INTO equity_accounts (user_id, initial_cash, cash) "
            "VALUES ('legacy', 1000000, 987654.32)"
        ))
        connection.execute(text("DROP TABLE equity_cash_events"))
        original_op = migration.op
        migration.op = Operations(MigrationContext.configure(connection))
        try:
            migration.upgrade()
            assert "equity_cash_events" in inspect(connection).get_table_names()
            assert connection.scalar(text(
                "SELECT cash FROM equity_accounts WHERE user_id = 'legacy'"
            )) == 987654.32
            assert connection.execute(text(
                "SELECT kind, delta, cash_after FROM equity_cash_events "
                "WHERE user_id = 'legacy'"
            )).one() == ("LEGACY_CHECKPOINT", 0, 987654.32)
        finally:
            migration.op = original_op
    engine.dispose()


def test_local_database_initializer_adds_exit_columns_without_dropping_existing_data(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "existing.sqlite3"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE paper_trades (id TEXT PRIMARY KEY, account_id TEXT, "
                "user_id TEXT, legacy_value TEXT)"
            )
        )
        connection.execute(
            text("INSERT INTO paper_trades (id, legacy_value) VALUES ('trade-1', 'preserved')")
        )
    engine.dispose()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    main()
    upgraded = create_engine(f"sqlite:///{database_path.as_posix()}")
    columns = {column["name"] for column in inspect(upgraded).get_columns("paper_trades")}
    with upgraded.connect() as connection:
        preserved = connection.execute(
            text("SELECT legacy_value FROM paper_trades WHERE id = 'trade-1'")
        ).scalar_one()
    upgraded.dispose()
    assert {
        "stop_loss_amount",
        "target_profit_amount",
        "trailing_stop_amount",
        "peak_unrealized_pnl",
        "exit_reason",
        "entry_rule_snapshot",
    }.issubset(columns)
    assert preserved == "preserved"
    get_settings.cache_clear()


def test_local_initializer_adds_validation_manifest_to_existing_table(tmp_path, monkeypatch):
    database_path = tmp_path / "existing-validation.sqlite3"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE equity_validation_studies (id TEXT PRIMARY KEY, "
            "legacy_value TEXT)"
        ))
        connection.execute(text(
            "INSERT INTO equity_validation_studies (id, legacy_value) "
            "VALUES ('study-1', 'preserved')"
        ))
    engine.dispose()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    main()
    upgraded = create_engine(f"sqlite:///{database_path.as_posix()}")
    columns = {column["name"] for column in inspect(upgraded).get_columns(
        "equity_validation_studies"
    )}
    with upgraded.connect() as connection:
        preserved = connection.execute(text(
            "SELECT legacy_value FROM equity_validation_studies WHERE id = 'study-1'"
        )).scalar_one()
    upgraded.dispose()
    assert "dataset_manifest" in columns
    assert preserved == "preserved"
    get_settings.cache_clear()


def test_audit_observation_index_migration_preserves_existing_events(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "audit_index_migration", Path("alembic/versions/0020_audit_event_correlation_index.py")
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{(tmp_path / 'audit-index.db').as_posix()}")
    Base.metadata.create_all(engine)
    event_id = uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(
            "DROP INDEX ix_audit_events_owner_correlation_type_created"
        ))
        connection.execute(text(
            "INSERT INTO audit_events (id, event_type, details) "
            "VALUES (:event_id, 'paper.trade.exit_checked', '{}')"
        ), {"event_id": event_id})
        original_op = migration.op
        migration.op = Operations(MigrationContext.configure(connection))
        try:
            migration.upgrade()
            assert "ix_audit_events_owner_correlation_type_created" in {
                item["name"] for item in inspect(connection).get_indexes("audit_events")
            }
            assert connection.execute(text(
                "SELECT COUNT(*) FROM audit_events WHERE id = :event_id"
            ), {"event_id": event_id}).scalar_one() == 1
            migration.downgrade()
            assert "ix_audit_events_owner_correlation_type_created" not in {
                item["name"] for item in inspect(connection).get_indexes("audit_events")
            }
        finally:
            migration.op = original_op
    engine.dispose()
