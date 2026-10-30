"""Workspace isolation, layer by layer, including attempts to bypass each layer.

Layers (see docs/data-model.md):
  1. membership check in `workspace_transaction`
  2. explicit workspace filters in the repository
  3. row-level security on the application role
  4. composite foreign keys (tested in test_constraints.py)
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from evidentai.config import APP_ROLE
from evidentai.db.errors import Forbidden, NotFound
from evidentai.db.models import TENANT_TABLES, DocumentKind, VersionStatus
from evidentai.db.scope import set_workspace, workspace_transaction
from tests.db.conftest import app_url_for

ALL_SCOPED = ("workspaces",) + TENANT_TABLES


def ws_column(table: str) -> str:
    return "id" if table == "workspaces" else "workspace_id"


# --- layer 1: membership ---------------------------------------------------------------


def test_non_member_is_forbidden(app_engine, two_workspaces):
    a, b = two_workspaces
    with pytest.raises(Forbidden), workspace_transaction(app_engine, a.user_id, b.workspace_id):
        pass


# --- layer 2: repository filters -------------------------------------------------------


def test_repository_treats_other_workspace_ids_as_not_found(app_engine, two_workspaces):
    a, b = two_workspaces
    with workspace_transaction(app_engine, a.user_id, a.workspace_id) as repo:
        calls = [
            lambda: repo.get_version(b.version_id),
            lambda: repo.transition(b.version_id, VersionStatus.ACTIVE, VersionStatus.DELETED),
            lambda: repo.create_release(b.product_id, "9.9"),
            lambda: repo.create_document(b.product_id, "x", DocumentKind.GUIDE),
            lambda: repo.create_snapshot(b.product_id, b.release_ids[:1]),
            lambda: repo.pinned_versions(b.snapshot_id),
            lambda: repo.close_snapshot(b.snapshot_id),
        ]
        for call in calls:
            with pytest.raises(NotFound):
                call()
    with workspace_transaction(app_engine, b.user_id, b.workspace_id) as repo:
        assert repo.get_version(b.version_id).status == "active"  # untouched


# --- layer 3: row-level security, with the repository bypassed -------------------------


def test_every_workspace_scoped_table_has_rls(owner_engine):
    """Fails if a table with a workspace_id column is added without RLS and a policy."""
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity,
                       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname)
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r' AND (
                  c.relname = 'workspaces' OR EXISTS (
                    SELECT 1 FROM information_schema.columns col
                    WHERE col.table_name = c.relname AND col.column_name = 'workspace_id'))
                """
            )
        ).all()
    assert {r[0] for r in rows} == set(ALL_SCOPED)
    assert all(r[1] and r[2] == 1 for r in rows), rows


def test_app_role_has_no_privilege_escape(owner_engine):
    with owner_engine.connect() as conn:
        role = conn.execute(
            text("SELECT rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname=:r"),
            {"r": APP_ROLE},
        ).one()
    assert tuple(role) == (False, False, False)


def test_no_workspace_set_sees_nothing(app_engine, two_workspaces):
    with app_engine.connect() as conn:
        for table in ALL_SCOPED:
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table


def test_raw_select_without_filter_returns_only_own_workspace(app_engine, two_workspaces):
    a, b = two_workspaces
    with app_engine.connect() as conn, conn.begin():
        set_workspace(conn, a.workspace_id)
        for table in ALL_SCOPED:
            col = ws_column(table)
            seen = set(conn.scalars(text(f"SELECT DISTINCT {col} FROM {table}")))
            assert seen == {a.workspace_id}, table


def test_cannot_write_into_other_workspace(app_engine, two_workspaces):
    a, b = two_workspaces
    with app_engine.connect() as conn:
        with conn.begin():
            set_workspace(conn, a.workspace_id)
            with pytest.raises(ProgrammingError) as exc:
                conn.execute(
                    text("INSERT INTO products (workspace_id, name) VALUES (:ws, 'evil')"),
                    {"ws": b.workspace_id},
                )
            assert exc.value.orig.sqlstate == "42501"  # row-level security violation
        with conn.begin():
            set_workspace(conn, a.workspace_id)
            changed = conn.execute(
                text("UPDATE document_versions SET filename = 'x' WHERE id = :v"),
                {"v": b.version_id},
            ).rowcount
            deleted = conn.execute(
                text("DELETE FROM chunks WHERE workspace_id = :ws"), {"ws": b.workspace_id}
            ).rowcount
            assert (changed, deleted) == (0, 0)
        with conn.begin():
            set_workspace(conn, a.workspace_id)
            with pytest.raises(ProgrammingError):  # moving a row into B is also blocked
                conn.execute(
                    text("UPDATE products SET workspace_id = :b WHERE id = :p"),
                    {"b": b.workspace_id, "p": a.product_id},
                )


def test_workspace_setting_does_not_leak_across_pooled_transactions(test_url, two_workspaces):
    a, _ = two_workspaces
    engine = create_engine(app_url_for(test_url), pool_size=1, max_overflow=0)
    try:
        with engine.connect() as conn:
            with conn.begin():
                set_workspace(conn, a.workspace_id)
                assert conn.scalar(text("SELECT count(*) FROM products")) == 1
            with conn.begin():
                assert conn.scalar(text("SELECT count(*) FROM products")) == 0
        with engine.connect() as conn:  # same pooled connection, new checkout
            assert conn.scalar(text("SELECT count(*) FROM products")) == 0
    finally:
        engine.dispose()


def test_invalid_workspace_setting_fails_closed(app_engine, two_workspaces):
    with app_engine.connect() as conn, conn.begin():
        conn.execute(text("SELECT set_config('app.workspace_id', 'not-a-uuid', true)"))
        with pytest.raises(DBAPIError):
            conn.execute(text("SELECT count(*) FROM products"))


@pytest.mark.parametrize(
    "statement",
    [
        "SET ROLE evidentai_owner",
        "ALTER TABLE chunks DISABLE ROW LEVEL SECURITY",
        "DROP POLICY tenant_isolation ON chunks",
        "SELECT count(*) FROM users",
        "CREATE TABLE sneaky (id int)",
        "TRUNCATE chunks",
    ],
)
def test_app_role_cannot_escalate(app_engine, two_workspaces, statement):
    with app_engine.connect() as conn, conn.begin(), pytest.raises(DBAPIError):
        conn.execute(text(statement))


def test_documented_boundary_rls_trusts_the_workspace_setting(app_engine, two_workspaces):
    """NOT a guarantee, a boundary: code running as the app role that sets
    `app.workspace_id` itself (skipping `workspace_transaction`) gets that workspace's rows.
    RLS stops forgotten filters and stray queries; it does not stop application code, or
    SQL injection, that chooses the wrong workspace. Membership is enforced only by the
    membership check in `workspace_transaction`."""
    a, b = two_workspaces
    with app_engine.connect() as conn, conn.begin():
        set_workspace(conn, b.workspace_id)  # user A never proved membership of B
        assert conn.scalar(text("SELECT count(*) FROM products")) == 1


def test_repository_filters_hold_even_without_rls(owner_engine, two_workspaces):
    """Layer 2 in isolation: the owner role bypasses RLS, so only the repository's own
    workspace filters stand between scope A and workspace B's rows here."""
    from evidentai.db.repository import WorkspaceRepository
    from evidentai.db.scope import WorkspaceScope

    a, b = two_workspaces
    with owner_engine.begin() as conn:
        repo = WorkspaceRepository(conn, WorkspaceScope(a.workspace_id, a.user_id, "owner"))
        with pytest.raises(NotFound):
            repo.get_version(b.version_id)
        with pytest.raises(NotFound):
            repo.pinned_versions(b.snapshot_id)
        with pytest.raises(NotFound):
            repo.create_document(b.product_id, "x", DocumentKind.GUIDE)
        assert repo.count_chunks(b.version_id) == 0
        assert repo.count_chunks(a.version_id) > 0
