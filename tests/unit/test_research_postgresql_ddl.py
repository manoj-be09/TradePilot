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
                     "0027_research_evaluation.py", "0028_research_evidence.py",
                     "0029_research_study.py", "0030_research_recommendation.py",
                     "0031_research_policy_set.py",
                     "0032_research_opportunity_event.py"):
            migration = runpy.run_path(str(root / name))
            assert migration["down_revision"] == previous
            previous = migration["revision"]
            migration["upgrade"]()
    sql = output.getvalue()
    assert sql.count("CREATE TABLE ") == 10
    assert "JSONB" in sql and "UUID" in sql
    for constraint in ("uq_research_candidate_definition", "uq_research_trial_manifest",
                       "uq_research_experiment_family", "uq_research_budget_trial",
                       "uq_research_evidence_trial_key", "uq_research_study_key",
                       "uq_research_recommendation_snapshot",
                       "uq_research_policy_set_study",
                       "uq_research_opportunity_event_key",
                       "ck_research_budget", "ck_research_slot", "ck_research_evidence_status"):
        assert constraint in sql
    assert "PRIMARY KEY (experiment_id, slot)" in sql
    assert "FOREIGN KEY(trial_id) REFERENCES research_trials (id)" in sql
    assert "ck_research_recommendation_decision" in sql
    assert "ck_research_opportunity_event_status" in sql
    assert not any(statement in sql.upper() for statement in (
        "DROP TABLE", "DELETE FROM", "UPDATE ",
    ))
