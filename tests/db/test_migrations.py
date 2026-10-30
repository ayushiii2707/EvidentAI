"""Migration round-trips on a dedicated disposable database, plus model/migration drift."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from evidentai.db.models import Base
from evidentai.db.safety import recreate_database
from tests.db.conftest import alembic_config

EXPECTED_TABLES = {
    "alembic_version",
    "users",
    "workspaces",
    "memberships",
    "products",
    "releases",
    "documents",
    "document_versions",
    "sections",
    "chunks",
    "embedding_models",
    "embeddings",
    "ingestion_jobs",
    "scope_snapshots",
    "scope_snapshot_versions",
}


@pytest.fixture(scope="module")
def migration_url(test_url):
    url = test_url.set(database="evidentai_migration_test")
    recreate_database(url)
    return url


def _state(url):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
            functions = set(
                conn.scalars(
                    text(
                        "SELECT p.proname FROM pg_proc p JOIN pg_namespace n "
                        "ON n.oid = p.pronamespace WHERE n.nspname = 'public' "
                        "AND p.proname LIKE 'evidentai_%'"
                    )
                )
            )
            policies = conn.scalar(text("SELECT count(*) FROM pg_policies"))
            vector = conn.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='vector'"))
            return tables, functions, policies, vector
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade(migration_url):
    cfg = alembic_config(migration_url)

    command.upgrade(cfg, "head")
    tables, functions, policies, vector = _state(migration_url)
    assert tables == EXPECTED_TABLES
    assert len(functions) == 7
    assert policies == 12  # workspaces + 11 workspace-scoped tables
    assert vector == 1

    command.downgrade(cfg, "base")
    assert _state(migration_url) == ({"alembic_version"}, set(), 0, 0)

    command.upgrade(cfg, "head")
    assert _state(migration_url)[0] == EXPECTED_TABLES


def test_models_match_migrated_schema(test_url):
    """Fails if a model changes without a migration (or the migration drifts)."""
    engine = create_engine(test_url)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()
    assert diff == []
