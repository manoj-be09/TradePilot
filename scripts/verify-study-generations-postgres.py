"""Verify migration 0024 inside a fresh PostgreSQL schema, then roll everything back."""

import importlib.util
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.engine import make_url

from packages.database.base import Base
from packages.database.models import EquityPolicyTrial, User


def verify() -> None:
    url = make_url(os.environ["DATABASE_URL"])
    if url.get_backend_name() != "postgresql":
        raise ValueError("PostgreSQL is required")
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0024_equity_study_generations.py"
    spec = importlib.util.spec_from_file_location("study_generations", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                # Generated identifier only; no user-supplied schema name or production table path.
                schema = "qa_study_generations_" + uuid4().hex
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                Base.metadata.create_all(connection)
                migration.op = Operations(MigrationContext.configure(connection))
                migration.downgrade()
                owner, root_id = uuid4(), uuid4()
                connection.execute(User.__table__.insert().values(
                    id=owner, email=f"{owner}@migration.invalid",
                    password_hash="unused", active=False,
                ))
                old = Table("equity_validation_studies", MetaData(), autoload_with=connection)
                data = dict(
                    id=root_id, user_id=owner, provider="test", instrument_key="NSE_EQ|TEST",
                    symbol="TEST", rule_version="v1", allocation=Decimal("10000"),
                    risk_budget=Decimal("200"), dataset_digest="a" * 64,
                    dataset_manifest={"preserved": True}, report_digest="b" * 64,
                    report={"preserved": True}, frozen_at=datetime.now(timezone.utc),
                )
                connection.execute(old.insert().values(**data))
                trial_id = uuid4()
                connection.execute(EquityPolicyTrial.__table__.insert().values(
                    id=trial_id, user_id=owner, study_id=root_id, strategy="trend",
                    registry_version="test-v1", trial_manifest={"preserved": True},
                    manifest_digest="c" * 64, result={"preserved": True}, result_digest="d" * 64,
                    created_at=datetime.now(timezone.utc),
                ))
                migration.upgrade()
                table = Table("equity_validation_studies", MetaData(), autoload_with=connection)
                root = connection.execute(select(table)).mappings().one()
                assert root["id"] == root_id and root["generation"] == 1
                assert root["parent_study_id"] is None
                assert root["dataset_manifest"] == root["report"] == {"preserved": True}
                assert root["dataset_digest"] == "a" * 64 and root["report_digest"] == "b" * 64
                trial = connection.execute(select(EquityPolicyTrial.__table__)).mappings().one()
                assert trial["id"] == trial_id and trial["study_id"] == root_id
                assert trial["result"] == {"preserved": True} and trial["result_digest"] == "d" * 64
                connection.execute(table.insert().values(
                    **{**data, "id": uuid4()}, generation=2, parent_study_id=root_id,
                ))
                try:
                    migration.downgrade()
                except RuntimeError as error:
                    assert "successor evidence" in str(error)
                else:
                    raise AssertionError("Lossy downgrade was accepted")
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        verify()
    except Exception:
        # Connection exceptions can contain credentials; do not echo any exception/URL.
        print(json.dumps({
            "status": "not_verified", "detail": "Database access or migration check failed",
        }))
        raise SystemExit(1) from None
    print(json.dumps({"status": "passed", "scope": "isolated schema; transaction rolled back"}))
