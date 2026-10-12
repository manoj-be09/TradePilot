"""Catch migration identifiers that SQLite accepts but PostgreSQL truncates."""

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_revision_chain_fits_alembic_version_column():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = list(scripts.walk_revisions())
    assert revisions
    assert len(scripts.get_heads()) == 1
    oversized = [row.revision for row in revisions if len(row.revision) > 32]
    assert not oversized, f"Revision IDs exceed Alembic's VARCHAR(32): {oversized}"
