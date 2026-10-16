"""Offline dialect compilation only; not PostgreSQL runtime acceptance."""

import io
import runpy
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_research_migrations_compile_additive_postgresql_ddl_without_connection():
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={
        "as_sql": True, "output_buffer": output,
    })
    root = Path(__file__).resolve().parents[2] / "alembic/versions"
    previous = "0024_equity_study_generations"
    with Operations.context(context):
        for name in ("0025_research_registry.py", "0026_research_budget.py",
                     "0027_research_evaluation.py"):
            migration = runpy.run_path(str(root / name))
            assert migration["down_revision"] == previous
            previous = migration["revision"]
            migration["upgrade"]()
    sql = output.getvalue()
    assert sql.count("CREATE TABLE ") == 5
    assert "JSONB" in sql and "UUID" in sql
    for constraint in ("uq_research_candidate_definition", "uq_research_trial_manifest",
                       "uq_research_experiment_family", "uq_research_budget_trial",
                       "ck_research_budget", "ck_research_slot"):
        assert constraint in sql
    assert "PRIMARY KEY (experiment_id, slot)" in sql
    assert "FOREIGN KEY(trial_id) REFERENCES research_trials (id)" in sql
    assert not any(statement in sql.upper() for statement in (
        "DROP TABLE", "DELETE FROM", "UPDATE ",
    ))
