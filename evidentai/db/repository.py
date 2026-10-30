"""Repository for tenant data. Constructed only by `workspace_transaction`.

Every statement filters on `scope.workspace_id` explicitly. Row-level security enforces
the same restriction underneath, so a query that forgets the filter still cannot see
another workspace; tests/db/test_isolation.py exercises both layers separately.
"""

import re
import uuid
from dataclasses import dataclass

from psycopg import errors as pg
from sqlalchemy import Connection, Row, and_, func, insert, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from evidentai.db.errors import (
    ConcurrentModification,
    IllegalTransition,
    NotFound,
    VersionPinned,
)
from evidentai.db.lifecycle import is_allowed
from evidentai.db.models import (
    Chunk,
    Document,
    DocumentKind,
    DocumentVersion,
    Product,
    Release,
    ScopeSnapshot,
    ScopeSnapshotVersion,
    Section,
    SnapshotState,
    VersionStatus,
)
from evidentai.db.scope import WorkspaceScope
from evidentai.ingestion.models import Chunk as ParsedChunk
from evidentai.ingestion.models import ParsedDocument

products = Product.__table__
releases = Release.__table__
documents = Document.__table__
versions = DocumentVersion.__table__
sections = Section.__table__
chunks = Chunk.__table__
snapshots = ScopeSnapshot.__table__
snapshot_versions = ScopeSnapshotVersion.__table__


def release_sort_key(label: str) -> list[int] | None:
    """ "3.0.1" -> [3, 0, 1]; labels that are not dotted integers are not orderable."""
    if re.fullmatch(r"\d+(\.\d+)*", label):
        return [int(p) for p in label.split(".")]
    return None


@dataclass(frozen=True)
class NewVersion:
    id: uuid.UUID
    duplicate: bool  # True when identical bytes already existed for this document/release


@dataclass(frozen=True)
class PinnedVersion:
    document_version_id: uuid.UUID
    status: VersionStatus

    @property
    def content_available(self) -> bool:
        return self.status is not VersionStatus.PURGED


class WorkspaceRepository:
    def __init__(self, conn: Connection, scope: WorkspaceScope):
        self.conn = conn
        self.scope = scope
        self._ws = scope.workspace_id

    # --- products, releases, documents --------------------------------------------------

    def create_product(self, name: str) -> uuid.UUID:
        return self.conn.scalar(
            insert(products).values(workspace_id=self._ws, name=name).returning(products.c.id)
        )

    def create_release(self, product_id: uuid.UUID, label: str) -> uuid.UUID:
        self._require(products, product_id)
        return self.conn.scalar(
            insert(releases)
            .values(
                workspace_id=self._ws,
                product_id=product_id,
                label=label,
                sort_key=release_sort_key(label),
            )
            .returning(releases.c.id)
        )

    def create_document(self, product_id: uuid.UUID, slug: str, kind: DocumentKind) -> uuid.UUID:
        self._require(products, product_id)
        return self.conn.scalar(
            insert(documents)
            .values(workspace_id=self._ws, product_id=product_id, slug=slug, kind=kind.value)
            .returning(documents.c.id)
        )

    # --- document versions --------------------------------------------------------------

    def create_version(
        self,
        document_id: uuid.UUID,
        release_id: uuid.UUID,
        *,
        source_sha256: str,
        filename: str,
        media_type: str,
        byte_size: int,
        storage_key: str,
    ) -> NewVersion:
        """Register an upload. Identical bytes for the same document and release return the
        existing version (`duplicate=True`) instead of creating a second one."""
        doc = self._require(documents, document_id)
        stmt = (
            pg_insert(versions)
            .values(
                workspace_id=self._ws,
                product_id=doc.product_id,
                document_id=document_id,
                release_id=release_id,
                source_sha256=source_sha256,
                filename=filename,
                media_type=media_type,
                byte_size=byte_size,
                storage_key=storage_key,
            )
            .on_conflict_do_nothing(constraint="uq_versions_doc_release_sha")
            .returning(versions.c.id)
        )
        new_id = self.conn.scalar(stmt)
        if new_id is not None:
            return NewVersion(new_id, duplicate=False)
        existing = self.conn.scalar(
            select(versions.c.id).where(
                versions.c.workspace_id == self._ws,
                versions.c.document_id == document_id,
                versions.c.release_id == release_id,
                versions.c.source_sha256 == source_sha256,
            )
        )
        return NewVersion(existing, duplicate=True)

    def get_version(self, version_id: uuid.UUID) -> Row:
        return self._require(versions, version_id)

    def transition(
        self, version_id: uuid.UUID, expected: VersionStatus, target: VersionStatus, **fields
    ) -> None:
        """Compare-and-set status change. Fails if the transition is not allowed, or if the
        row is no longer in `expected` (e.g. another worker moved it first)."""
        if not is_allowed(expected, target):
            raise IllegalTransition(f"{expected} -> {target}")
        try:
            result = self.conn.execute(
                update(versions)
                .where(
                    versions.c.id == version_id,
                    versions.c.workspace_id == self._ws,
                    versions.c.status == expected.value,
                )
                .values(status=target.value, **fields)
            )
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if result.rowcount == 0:
            current = self._require(versions, version_id).status
            raise ConcurrentModification(f"expected {expected}, found {current}")

    def activate(self, version_id: uuid.UUID) -> uuid.UUID | None:
        """indexing -> active, superseding the previously active version of the same
        document and release in the same transaction. Returns the superseded id, if any."""
        v = self._require(versions, version_id)
        previous = self.conn.scalar(
            select(versions.c.id)
            .where(
                versions.c.workspace_id == self._ws,
                versions.c.document_id == v.document_id,
                versions.c.release_id == v.release_id,
                versions.c.status == VersionStatus.ACTIVE.value,
            )
            .with_for_update()
        )
        if previous is not None:
            self.transition(previous, VersionStatus.ACTIVE, VersionStatus.SUPERSEDED)
        self.transition(version_id, VersionStatus.INDEXING, VersionStatus.ACTIVE)
        return previous

    def purge(self, version_id: uuid.UUID) -> None:
        """deleted -> purged. The database removes sections and chunks and refuses while an
        open scope snapshot pins the version."""
        self.transition(version_id, VersionStatus.DELETED, VersionStatus.PURGED)

    def write_content(
        self, version_id: uuid.UUID, doc: ParsedDocument, parsed_chunks: list[ParsedChunk]
    ) -> int:
        """Insert sections and chunks for a version in status `indexing` (the database
        rejects content writes in any other status). Returns the number of chunks."""
        self._require(versions, version_id)
        ids: dict[str, uuid.UUID] = {}
        for s in doc.sections:
            ids[s.key] = self.conn.scalar(
                insert(sections)
                .values(
                    workspace_id=self._ws,
                    document_version_id=version_id,
                    parent_id=ids.get(s.parent_key) if s.parent_key else None,
                    section_key=s.key,
                    ordinal=s.ordinal,
                    level=s.level,
                    heading=s.heading,
                    heading_path=s.heading_path,
                    page=s.heading_provenance.page,
                    line=s.heading_provenance.line,
                )
                .returning(sections.c.id)
            )
        if parsed_chunks:
            self.conn.execute(
                insert(chunks),
                [
                    dict(
                        workspace_id=self._ws,
                        document_version_id=version_id,
                        section_id=ids[c.section_key],
                        chunk_key=c.key,
                        ordinal=c.ordinal,
                        text=c.text,
                        embed_text=c.embed_text,
                        content_hash=c.content_hash,
                        word_count=c.word_count,
                        page_start=c.page_start,
                        page_end=c.page_end,
                        line_start=c.line_start,
                        line_end=c.line_end,
                    )
                    for c in parsed_chunks
                ],
            )
        return len(parsed_chunks)

    def count_chunks(self, version_id: uuid.UUID) -> int:
        return len(
            self.conn.execute(
                select(chunks.c.id).where(
                    chunks.c.workspace_id == self._ws, chunks.c.document_version_id == version_id
                )
            ).all()
        )

    # --- scope snapshots ----------------------------------------------------------------

    def create_snapshot(self, product_id: uuid.UUID, release_ids: list[uuid.UUID]) -> uuid.UUID:
        """Pin the versions that are active *now* for the given releases. Later uploads,
        replacements or deletions do not change what the snapshot contains."""
        self._require(products, product_id)
        snapshot_id = self.conn.scalar(
            insert(snapshots)
            .values(workspace_id=self._ws, product_id=product_id, release_ids=release_ids)
            .returning(snapshots.c.id)
        )
        # One INSERT ... SELECT: the set of active versions is read in a single statement.
        self.conn.execute(
            insert(snapshot_versions).from_select(
                ["snapshot_id", "document_version_id", "workspace_id"],
                select(literal(snapshot_id), versions.c.id, versions.c.workspace_id).where(
                    versions.c.workspace_id == self._ws,
                    versions.c.product_id == product_id,
                    versions.c.release_id.in_(release_ids),
                    versions.c.status == VersionStatus.ACTIVE.value,
                ),
            )
        )
        return snapshot_id

    def close_snapshot(self, snapshot_id: uuid.UUID) -> None:
        self._require(snapshots, snapshot_id)
        self.conn.execute(
            update(snapshots)
            .where(
                snapshots.c.id == snapshot_id,
                snapshots.c.workspace_id == self._ws,
                snapshots.c.state == SnapshotState.OPEN.value,
            )
            .values(state=SnapshotState.CLOSED.value, closed_at=func.now())
        )

    def pinned_versions(self, snapshot_id: uuid.UUID) -> list[PinnedVersion]:
        """What a snapshot pinned, with each version's current status. A purged version is
        reported as such rather than silently dropped."""
        self._require(snapshots, snapshot_id)
        rows = self.conn.execute(
            select(versions.c.id, versions.c.status)
            .join(
                snapshot_versions,
                and_(
                    snapshot_versions.c.document_version_id == versions.c.id,
                    snapshot_versions.c.workspace_id == versions.c.workspace_id,
                ),
            )
            .where(
                snapshot_versions.c.snapshot_id == snapshot_id,
                snapshot_versions.c.workspace_id == self._ws,
            )
            .order_by(versions.c.id)
        )
        return [PinnedVersion(r.id, VersionStatus(r.status)) for r in rows]

    # --- helpers ------------------------------------------------------------------------

    def _require(self, table, row_id: uuid.UUID) -> Row:
        row = self.conn.execute(
            select(table).where(table.c.id == row_id, table.c.workspace_id == self._ws)
        ).first()
        if row is None:
            raise NotFound(f"{table.name} {row_id}")
        return row


def _translate(exc: DBAPIError) -> Exception:
    orig = exc.orig
    if isinstance(orig, pg.ObjectInUse):
        return VersionPinned(str(orig).splitlines()[0])
    if isinstance(orig, pg.CheckViolation):
        return IllegalTransition(str(orig).splitlines()[0])
    return exc
