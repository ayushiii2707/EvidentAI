"""Foreign keys, cascades, duplicate detection, content and snapshot integrity."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from evidentai.db.errors import IllegalTransition, VersionPinned
from evidentai.db.models import TENANT_TABLES, DocumentKind, VersionStatus
from evidentai.db.scope import set_workspace, workspace_transaction
from evidentai.ingestion import chunk_document, parse_document
from tests.db.conftest import SAMPLE_MD, walk_to

S = VersionStatus


def repo_for(app_engine, seeded):
    return workspace_transaction(app_engine, seeded.user_id, seeded.workspace_id)


# --- cross-workspace references are rejected by composite foreign keys -------------------


def test_document_cannot_reference_other_workspace_product(owner_engine, two_workspaces):
    a, b = two_workspaces
    # The owner role bypasses RLS, so only the composite foreign key can stop this.
    with owner_engine.connect() as conn, conn.begin(), pytest.raises(IntegrityError) as exc:
        conn.execute(
            text(
                "INSERT INTO documents (workspace_id, product_id, slug, kind) "
                "VALUES (:ws, :p, 'x', 'guide')"
            ),
            {"ws": a.workspace_id, "p": b.product_id},
        )
    assert "fk_documents_product" in str(exc.value)


def test_version_release_must_belong_to_document_product(app_engine, two_workspaces):
    a, _ = two_workspaces
    with repo_for(app_engine, a) as repo:
        other_product = repo.create_product("Other")
        other_release = repo.create_release(other_product, "9.0")
        with pytest.raises(IntegrityError) as exc:
            repo.create_version(
                a.document_id,
                other_release,
                source_sha256="b" * 64,
                filename="f",
                media_type="m",
                byte_size=1,
                storage_key="k",
            )
    assert "fk_versions_release" in str(exc.value)


def test_chunk_section_must_belong_to_same_version(owner_engine, two_workspaces):
    a, _ = two_workspaces
    with owner_engine.connect() as conn, conn.begin(), pytest.raises(DBAPIError):
        section = conn.scalar(
            text("SELECT id FROM sections WHERE document_version_id = :v LIMIT 1"),
            {"v": a.version_id},
        )
        conn.execute(
            text(
                "INSERT INTO chunks (workspace_id, document_version_id, section_id, chunk_key, "
                "ordinal, text, embed_text, content_hash, word_count) "
                "VALUES (:ws, gen_random_uuid(), :s, 'k', 0, 't', 't', :h, 1)"
            ),
            {"ws": a.workspace_id, "s": section, "h": "c" * 64},
        )


# --- cascades and deletion -------------------------------------------------------------


def _counts(owner_engine, workspace_id):
    with owner_engine.connect() as conn:
        out = {}
        for table in TENANT_TABLES:
            col = "workspace_id"
            out[table] = conn.scalar(
                text(f"SELECT count(*) FROM {table} WHERE {col} = :ws"), {"ws": workspace_id}
            )
        out["workspaces"] = conn.scalar(
            text("SELECT count(*) FROM workspaces WHERE id = :ws"), {"ws": workspace_id}
        )
        return out


def test_deleting_workspace_cascades_everything_and_spares_others(owner_engine, two_workspaces):
    a, b = two_workspaces
    before_a, before_b = (
        _counts(owner_engine, a.workspace_id),
        _counts(owner_engine, b.workspace_id),
    )
    assert all(n > 0 for n in before_a.values()), before_a  # seed touches every table
    with owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM workspaces WHERE id = :ws"), {"ws": a.workspace_id})
    assert all(n == 0 for n in _counts(owner_engine, a.workspace_id).values())
    assert _counts(owner_engine, b.workspace_id) == before_b


def test_pinned_version_cannot_be_hard_deleted(owner_engine, two_workspaces):
    a, _ = two_workspaces
    with owner_engine.connect() as conn, conn.begin(), pytest.raises(IntegrityError) as exc:
        conn.execute(text("DELETE FROM document_versions WHERE id = :v"), {"v": a.version_id})
    assert "fk_snapshot_versions_version" in str(exc.value)


def test_embedding_model_in_use_cannot_be_deleted(owner_engine, two_workspaces):
    a, _ = two_workspaces
    with owner_engine.connect() as conn, conn.begin(), pytest.raises(IntegrityError):
        conn.execute(text("DELETE FROM embedding_models WHERE id = :m"), {"m": a.extra["model_id"]})


# --- duplicates, content, embeddings ---------------------------------------------------


def test_duplicate_upload_returns_existing_version(app_engine, two_workspaces):
    a, _ = two_workspaces
    sha = parse_document("guide.md", SAMPLE_MD).source_sha256
    with repo_for(app_engine, a) as repo:
        again = repo.create_version(
            a.document_id,
            a.release_ids[0],
            source_sha256=sha,
            filename="renamed.md",
            media_type="text/markdown",
            byte_size=len(SAMPLE_MD),
            storage_key="other",
        )
        assert again.duplicate and again.id == a.version_id
        different = repo.create_version(
            a.document_id,
            a.release_ids[0],
            source_sha256="d" * 64,
            filename="guide.md",
            media_type="text/markdown",
            byte_size=10,
            storage_key="k2",
        )
        assert not different.duplicate and different.id != a.version_id


def test_content_only_writable_while_indexing(app_engine, two_workspaces):
    a, _ = two_workspaces
    parsed = parse_document("guide.md", SAMPLE_MD)
    with repo_for(app_engine, a) as repo, pytest.raises(DBAPIError, match="status active"):
        repo.write_content(a.version_id, parsed, chunk_document(parsed))


def test_embedding_dimensions_must_match_model(app_engine, two_workspaces):
    a, _ = two_workspaces
    with app_engine.connect() as conn, conn.begin():
        set_workspace(conn, a.workspace_id)
        with pytest.raises(DBAPIError, match="has 3 dimensions"):
            conn.execute(
                text(
                    "INSERT INTO embeddings (workspace_id, model_id, content_hash, embedding) "
                    "VALUES (:ws, :m, :h, '[1,2,3]')"
                ),
                {"ws": a.workspace_id, "m": a.extra["model_id"], "h": "e" * 64},
            )


def test_version_identity_columns_are_immutable(app_engine, two_workspaces):
    a, _ = two_workspaces
    with repo_for(app_engine, a) as repo:
        other_doc = repo.create_document(a.product_id, "other", DocumentKind.REFERENCE)
    with app_engine.connect() as conn, conn.begin():
        set_workspace(conn, a.workspace_id)
        with pytest.raises(DBAPIError, match="immutable"):
            conn.execute(
                text("UPDATE document_versions SET document_id = :d WHERE id = :v"),
                {"d": other_doc, "v": a.version_id},
            )


# --- snapshots, retention and purge ----------------------------------------------------


def _replace(repo, a, n):
    new = repo.create_version(
        a.document_id,
        a.release_ids[0],
        source_sha256=f"{n:064x}",
        filename="guide.md",
        media_type="text/markdown",
        byte_size=1,
        storage_key=f"k{n}",
    ).id
    walk_to(repo, new, S.ACTIVE)
    return new


def test_snapshot_keeps_pinned_version_after_replacement(app_engine, two_workspaces):
    a, _ = two_workspaces
    with repo_for(app_engine, a) as repo:
        new = _replace(repo, a, 1)
        pinned = repo.pinned_versions(a.snapshot_id)
        assert [p.document_version_id for p in pinned] == [a.version_id]
        assert pinned[0].status is S.SUPERSEDED and pinned[0].content_available
        assert repo.count_chunks(a.version_id) > 0  # superseded content is retained
        fresh = repo.create_snapshot(a.product_id, [a.release_ids[0]])
        assert [p.document_version_id for p in repo.pinned_versions(fresh)] == [new]


def test_purge_refused_while_open_snapshot_pins_version(app_engine, two_workspaces):
    a, _ = two_workspaces
    with repo_for(app_engine, a) as repo:
        _replace(repo, a, 2)
        repo.transition(a.version_id, S.SUPERSEDED, S.DELETED)
        with pytest.raises(VersionPinned):
            repo.purge(a.version_id)


def test_purge_after_close_removes_content_and_is_reported(app_engine, two_workspaces):
    a, _ = two_workspaces
    with repo_for(app_engine, a) as repo:
        _replace(repo, a, 3)
        repo.transition(a.version_id, S.SUPERSEDED, S.DELETED)
        assert repo.count_chunks(a.version_id) > 0  # deleted: hidden, content retained
        repo.close_snapshot(a.snapshot_id)
        repo.purge(a.version_id)
        assert repo.count_chunks(a.version_id) == 0
        assert repo.get_version(a.version_id).status == "purged"  # tombstone kept
        pinned = repo.pinned_versions(a.snapshot_id)
        assert pinned[0].status is S.PURGED and not pinned[0].content_available


def test_snapshot_only_pins_active_versions_in_scope(app_engine, two_workspaces):
    a, _ = two_workspaces
    with repo_for(app_engine, a) as repo:
        pending = repo.create_version(
            a.document_id,
            a.release_ids[1],
            source_sha256="f" * 64,
            filename="g",
            media_type="m",
            byte_size=1,
            storage_key="k",
        ).id
        snap = repo.create_snapshot(a.product_id, a.release_ids)
        assert pending not in [p.document_version_id for p in repo.pinned_versions(snap)]
    with app_engine.connect() as conn, conn.begin():
        set_workspace(conn, a.workspace_id)
        with pytest.raises(DBAPIError, match="only active versions"):
            conn.execute(
                text("INSERT INTO scope_snapshot_versions VALUES (:s, :v, :ws)"),
                {"s": snap, "v": pending, "ws": a.workspace_id},
            )


def test_snapshot_scope_rules(app_engine, two_workspaces):
    a, _ = two_workspaces
    with repo_for(app_engine, a) as repo:
        other_product = repo.create_product("Other")
        foreign_release = repo.create_release(other_product, "1.0")
    with app_engine.connect() as conn:
        with conn.begin():
            set_workspace(conn, a.workspace_id)
            with pytest.raises(DBAPIError, match="belong to its product"):
                conn.execute(
                    text(
                        "INSERT INTO scope_snapshots (workspace_id, product_id, release_ids) "
                        "VALUES (:ws, :p, ARRAY[CAST(:r AS uuid)])"
                    ),
                    {"ws": a.workspace_id, "p": a.product_id, "r": foreign_release},
                )
        with conn.begin():
            set_workspace(conn, a.workspace_id)
            conn.execute(
                text("UPDATE scope_snapshots SET state = 'closed' WHERE id = :s"),
                {"s": a.snapshot_id},
            )
            with pytest.raises(DBAPIError, match="cannot be reopened"):
                conn.execute(
                    text("UPDATE scope_snapshots SET state = 'open' WHERE id = :s"),
                    {"s": a.snapshot_id},
                )


def test_purged_is_terminal(app_engine, two_workspaces):
    a, _ = two_workspaces
    with repo_for(app_engine, a) as repo:
        repo.close_snapshot(a.snapshot_id)
        repo.transition(a.version_id, S.ACTIVE, S.DELETED)
        repo.purge(a.version_id)
        for target in S:
            if target is not S.PURGED:
                with pytest.raises(IllegalTransition):
                    repo.transition(a.version_id, S.PURGED, target)
