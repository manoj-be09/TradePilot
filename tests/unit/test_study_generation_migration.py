import importlib.util
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlalchemy.exc import IntegrityError

from packages.database import models  # noqa: F401
from packages.database.base import Base


def test_study_generation_migration_preserves_evidence_and_refuses_lossy_downgrade(tmp_path):
    path = Path("alembic/versions/0024_equity_study_generations.py")
    spec = importlib.util.spec_from_file_location("study_generations", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'generations.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.downgrade()
        old = Table("equity_validation_studies", MetaData(), autoload_with=connection)
        root_id = uuid4().hex
        data = dict(
            id=root_id, user_id=uuid4().hex, provider="test", instrument_key="NSE_EQ|TEST",
            symbol="TEST", rule_version="v1", allocation=Decimal("10000"),
            risk_budget=Decimal("200"), dataset_digest="a" * 64,
            dataset_manifest={"preserved": True}, report_digest="b" * 64,
            report={"preserved": True}, frozen_at=datetime.now(timezone.utc),
        )
        connection.execute(old.insert().values(**data))
        migration.upgrade()
        table = Table("equity_validation_studies", MetaData(), autoload_with=connection)
        root = connection.execute(select(table)).mappings().one()
        assert root["id"] == root_id and root["generation"] == 1
        assert root["parent_study_id"] is None
        assert root["dataset_manifest"] == root["report"] == {"preserved": True}
        assert root["dataset_digest"] == "a" * 64 and root["report_digest"] == "b" * 64
        connection.execute(table.insert().values(
            **{**data, "id": uuid4().hex}, generation=2, parent_study_id=root_id,
        ))
        with pytest.raises(RuntimeError, match="successor evidence"):
            migration.downgrade()
        assert "generation" in {column["name"] for column in inspect(connection).get_columns(
            "equity_validation_studies"
        )}
        with pytest.raises(IntegrityError):
            connection.execute(table.insert().values(
                **{**data, "id": uuid4().hex}, generation=3, parent_study_id=root_id,
            ))
    engine.dispose()
