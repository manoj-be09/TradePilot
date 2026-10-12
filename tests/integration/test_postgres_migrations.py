import os
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command


def test_postgres_clean_and_existing_schema_upgrade(monkeypatch):
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set; migration execution is tested in CI")
    engine = create_engine(url)
    schema = "migration_" + uuid4().hex
    fresh_schema = "migration_fresh_" + uuid4().hex
    config = Config("alembic.ini")
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'CREATE SCHEMA "{fresh_schema}"'))
    try:
        with engine.begin() as connection:
            connection.execute(text(f'SET search_path TO "{schema}"'))
            config.attributes["connection"] = connection
            command.upgrade(config, "0007_paper_strategy_presets")
            assert "paper_entry_commands" not in inspect(connection).get_table_names(schema=schema)
            command.upgrade(config, "head")
            tables = inspect(connection).get_table_names(schema=schema)
            assert {
                "users", "paper_trades", "paper_entry_commands", "worker_leases",
                "equity_accounts", "equity_deployments", "equity_cash_events",
                "paper_deployments",
            } <= set(tables)
            assert "initial_cash" in {
                column["name"] for column in inspect(connection).get_columns("equity_accounts")
            }
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0023_equity_cash_journal"
            )
        with engine.begin() as connection:
            connection.execute(text(f'SET search_path TO "{fresh_schema}"'))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            fresh_tables = inspect(connection).get_table_names(schema=fresh_schema)
            assert {"users", "paper_trades", "paper_entry_commands", "worker_leases"} <= set(
                fresh_tables
            )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            connection.execute(text(f'DROP SCHEMA "{fresh_schema}" CASCADE'))
        engine.dispose()
