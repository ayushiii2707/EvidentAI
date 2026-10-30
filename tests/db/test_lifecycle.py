"""Document-version lifecycle: Python rules, database trigger, and concurrent claims."""

import importlib.util
import itertools
import threading

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from evidentai.db.errors import ConcurrentModification, IllegalTransition
from evidentai.db.lifecycle import ALLOWED_TRANSITIONS, is_allowed
from evidentai.db.models import DocumentKind, VersionStatus
from evidentai.db.scope import create_workspace, set_workspace, workspace_transaction
from tests.db.conftest import ROOT, create_user, walk_to

S = VersionStatus


def _migration_module():
    path = ROOT / "migrations" / "versions" / "0001_initial_schema.py"
    spec = importlib.util.spec_from_file_location("m0001", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_trigger_table_matches_python_rules():
    frozen = set(_migration_module().ALLOWED_TRANSITIONS)
    python = {(a.value, b.value) for a, targets in ALLOWED_TRANSITIONS.items() for b in targets}
    assert frozen == python


@pytest.fixture
def workspace(app_engine, owner_engine):
    user = create_user(owner_engine, "life@example.test")
    ws = create_workspace(app_engine, user, "lifecycle")
    with workspace_transaction(app_engine, user, ws) as repo:
        product = repo.create_product("P")
        release = repo.create_release(product, "1.0")
        doc = repo.create_document(product, "doc", DocumentKind.GUIDE)
    return user, ws, doc, release


def _new_version(repo, doc, release, n):
    return repo.create_version(
        doc,
        release,
        source_sha256=f"{n:064x}",
        filename="f.md",
        media_type="text/markdown",
        byte_size=1,
        storage_key=f"k{n}",
    ).id


@pytest.mark.parametrize(("current", "target"), list(itertools.product(S, S)))
def test_database_enforces_every_transition(current, target, app_engine, workspace):
    """All 64 (from, to) pairs, written with raw SQL so the repository is bypassed."""
    if current == target:
        pytest.skip("same-status writes are no-ops")
    user, ws, doc, release = workspace
    n = list(S).index(current) * 10 + list(S).index(target)
    with workspace_transaction(app_engine, user, ws) as repo:
        version = _new_version(repo, doc, release, n)
        walk_to(repo, version, current)
    with app_engine.connect() as conn:
        tx = conn.begin()
        set_workspace(conn, ws)
        try:
            conn.execute(
                text("UPDATE document_versions SET status = :s WHERE id = :id"),
                {"s": target.value, "id": version},
            )
            accepted = True
        except DBAPIError as exc:
            assert exc.orig.sqlstate == "23514", exc  # check_violation from the trigger
            accepted = False
        tx.rollback()
    assert accepted == is_allowed(current, target)


def test_versions_must_start_pending(app_engine, workspace):
    user, ws, doc, release = workspace
    with app_engine.connect() as conn, conn.begin():
        set_workspace(conn, ws)
        product = conn.scalar(text("SELECT product_id FROM documents WHERE id = :d"), {"d": doc})
        with pytest.raises(DBAPIError, match="must be created as pending"):
            conn.execute(
                text(
                    "INSERT INTO document_versions (workspace_id, product_id, document_id, "
                    "release_id, status, source_sha256, filename, media_type, byte_size, "
                    "storage_key) VALUES (:ws, :p, :d, :r, 'active', :sha, 'f', 'm', 1, 'k')"
                ),
                {"ws": ws, "p": product, "d": doc, "r": release, "sha": "a" * 64},
            )


def test_repository_rejects_illegal_transition_without_touching_db(app_engine, workspace):
    user, ws, doc, release = workspace
    with workspace_transaction(app_engine, user, ws) as repo:
        version = _new_version(repo, doc, release, 1)
        with pytest.raises(IllegalTransition):
            repo.transition(version, S.PENDING, S.ACTIVE)
        assert repo.get_version(version).status == "pending"


def test_stale_expected_status_is_detected(app_engine, workspace):
    user, ws, doc, release = workspace
    with workspace_transaction(app_engine, user, ws) as repo:
        version = _new_version(repo, doc, release, 2)
        repo.transition(version, S.PENDING, S.PARSING)
        with pytest.raises(ConcurrentModification, match="found parsing"):
            repo.transition(version, S.PENDING, S.PARSING)


def test_concurrent_claims_only_one_wins(app_engine, workspace):
    """Two workers claim the same pending version at once; the row lock serializes them
    and the loser's compare-and-set matches zero rows."""
    user, ws, doc, release = workspace
    with workspace_transaction(app_engine, user, ws) as repo:
        version = _new_version(repo, doc, release, 3)

    first_updated = threading.Event()
    results: dict[str, str] = {}

    def worker(name: str, hold: bool):
        try:
            with workspace_transaction(app_engine, user, ws) as repo:
                if not hold:
                    first_updated.wait(5)
                repo.transition(version, S.PENDING, S.PARSING)
                if hold:
                    first_updated.set()
                    threading.Event().wait(0.3)  # keep the row locked while the other waits
            results[name] = "won"
        except ConcurrentModification:
            results[name] = "lost"

    threads = [
        threading.Thread(target=worker, args=("a", True)),
        threading.Thread(target=worker, args=("b", False)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert results == {"a": "won", "b": "lost"}


def test_activation_supersedes_previous_active(app_engine, workspace):
    user, ws, doc, release = workspace
    with workspace_transaction(app_engine, user, ws) as repo:
        old = _new_version(repo, doc, release, 4)
        walk_to(repo, old, S.ACTIVE)
        new = _new_version(repo, doc, release, 5)
        walk_to(repo, new, S.INDEXING)
        assert repo.activate(new) == old
        assert repo.get_version(old).status == "superseded"
        assert repo.get_version(new).status == "active"


def test_database_allows_only_one_active_version(app_engine, workspace):
    """Even bypassing `activate()`, a second active version for the same document and
    release violates the partial unique index."""
    user, ws, doc, release = workspace
    with workspace_transaction(app_engine, user, ws) as repo:
        first = _new_version(repo, doc, release, 6)
        walk_to(repo, first, S.ACTIVE)
        second = _new_version(repo, doc, release, 7)
        walk_to(repo, second, S.INDEXING)
    with app_engine.connect() as conn, conn.begin():
        set_workspace(conn, ws)
        with pytest.raises(DBAPIError) as exc:
            conn.execute(
                text("UPDATE document_versions SET status = 'active' WHERE id = :id"),
                {"id": second},
            )
        assert exc.value.orig.sqlstate == "23505"  # unique_violation
