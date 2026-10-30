"""ORM models: the tables CP2 and CP3 (ingestion) need. Later tables are listed in
docs/data-model.md and are not created early.

Tenant-integrity conventions:
- Every tenant-owned row carries `workspace_id`.
- Child rows reference parents through composite foreign keys that include
  `workspace_id` (and `product_id` where versions must match), so the database itself
  rejects a row that points at another workspace's or product's parent.
- Row-level security policies (created in the migration) restrict the application role
  to the workspace set in the transaction-local setting `app.workspace_id`.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DocumentKind(StrEnum):
    REFERENCE = "reference"
    GUIDE = "guide"
    RELEASE_NOTES = "release_notes"
    MIGRATION = "migration"
    TROUBLESHOOTING = "troubleshooting"


class VersionStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    INDEXING = "indexing"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    DELETED = "deleted"
    PURGED = "purged"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SnapshotState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


def _in(column: str, enum: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(m.value) for m in enum)})"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _workspace_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )


# --- Identity and tenancy ---------------------------------------------------------------


class User(Base):
    """Global (not tenant-owned). Credentials and sessions arrive with real auth in CP7."""

    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    __table_args__ = (Index("uq_users_email_lower", func.lower(text("email")), unique=True),)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class Membership(Base):
    __tablename__ = "memberships"
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    __table_args__ = (CheckConstraint("role IN ('owner', 'member')", name="ck_memberships_role"),)


# --- Documents and versions -------------------------------------------------------------


class Product(Base):
    __tablename__ = "products"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_products_id_workspace"),
        Index("uq_products_workspace_name", "workspace_id", func.lower(text("name")), unique=True),
    )


class Release(Base):
    __tablename__ = "releases"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    # Numeric components of the label ("3.0.1" -> {3,0,1}) for ordering; NULL if not numeric.
    sort_key: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    created_at: Mapped[datetime] = _created_at()
    __table_args__ = (
        ForeignKeyConstraint(
            ["product_id", "workspace_id"],
            ["products.id", "products.workspace_id"],
            ondelete="CASCADE",
            name="fk_releases_product",
        ),
        UniqueConstraint("product_id", "label", name="uq_releases_product_label"),
        UniqueConstraint("id", "product_id", "workspace_id", name="uq_releases_id_product_ws"),
    )


class Document(Base):
    """A logical document (e.g. "auth-guide") that has one version per release."""

    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    __table_args__ = (
        ForeignKeyConstraint(
            ["product_id", "workspace_id"],
            ["products.id", "products.workspace_id"],
            ondelete="CASCADE",
            name="fk_documents_product",
        ),
        CheckConstraint(_in("kind", DocumentKind), name="ck_documents_kind"),
        CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{0,99}$'", name="ck_documents_slug"),
        UniqueConstraint("product_id", "slug", name="uq_documents_product_slug"),
        UniqueConstraint("id", "product_id", "workspace_id", name="uq_documents_id_product_ws"),
    )


class DocumentVersion(Base):
    """One uploaded file of a document for one release, with its lifecycle status.

    Rows are never hard-deleted while anything references them: purging removes the
    sections and chunks but keeps this row as a tombstone, so investigations that cited
    it can report that the evidence was deleted.
    """

    __tablename__ = "document_versions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    release_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=VersionStatus.PENDING.value
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    parser: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "product_id", "workspace_id"],
            ["documents.id", "documents.product_id", "documents.workspace_id"],
            ondelete="CASCADE",
            name="fk_versions_document",
        ),
        ForeignKeyConstraint(
            ["release_id", "product_id", "workspace_id"],
            ["releases.id", "releases.product_id", "releases.workspace_id"],
            ondelete="CASCADE",
            name="fk_versions_release",
        ),
        CheckConstraint(_in("status", VersionStatus), name="ck_versions_status"),
        CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_versions_sha"),
        CheckConstraint("byte_size > 0", name="ck_versions_size"),
        # Duplicate detection: identical bytes for the same document and release.
        UniqueConstraint(
            "document_id", "release_id", "source_sha256", name="uq_versions_doc_release_sha"
        ),
        UniqueConstraint("id", "workspace_id", name="uq_versions_id_workspace"),
        # At most one active version of a document per release.
        Index(
            "uq_versions_one_active",
            "document_id",
            "release_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class Section(Base):
    __tablename__ = "sections"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE")
    )
    section_key: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    heading: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    line: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_version_id", "workspace_id"],
            ["document_versions.id", "document_versions.workspace_id"],
            ondelete="CASCADE",
            name="fk_sections_version",
        ),
        UniqueConstraint("document_version_id", "ordinal", name="uq_sections_version_ordinal"),
        UniqueConstraint("document_version_id", "section_key", name="uq_sections_version_key"),
        UniqueConstraint("id", "document_version_id", "workspace_id", name="uq_sections_id_v_ws"),
        CheckConstraint("level >= 0", name="ck_sections_level"),
    )


class Chunk(Base):
    """Retrievable text with provenance. `content_hash` links to a reusable embedding;
    the chunk row, not the embedding, records which document version the text came from."""

    __tablename__ = "chunks"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    section_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chunk_key: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embed_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(
            ["section_id", "document_version_id", "workspace_id"],
            ["sections.id", "sections.document_version_id", "sections.workspace_id"],
            ondelete="CASCADE",
            name="fk_chunks_section",
        ),
        UniqueConstraint("document_version_id", "chunk_key", name="uq_chunks_version_key"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_chunks_hash"),
        Index("ix_chunks_version", "document_version_id"),
        Index("ix_chunks_content_hash", "workspace_id", "content_hash"),
    )


# --- Embedding reuse --------------------------------------------------------------------


class EmbeddingModel(Base):
    """Global catalog of embedding model identities. A new model, revision or text
    normalization is a new row, so embeddings from different identities never mix."""

    __tablename__ = "embedding_models"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    normalization_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    __table_args__ = (
        UniqueConstraint(
            "name", "revision", "normalization_version", name="uq_embedding_models_identity"
        ),
        CheckConstraint("dimensions BETWEEN 1 AND 4096", name="ck_embedding_models_dims"),
    )


class Embedding(Base):
    """Embedding keyed by (workspace, model, normalized-content hash).

    Scoped per workspace rather than shared globally: a global cache would let one tenant
    infer that another holds identical text (through cache hits), and it would complicate
    deletion. Reuse within a workspace covers the main case: unchanged sections across
    versions. Rows are not tied to a document version; unreferenced rows are removed by a
    garbage-collection pass (deferred).
    """

    __tablename__ = "embeddings"
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    model_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("embedding_models.id", ondelete="RESTRICT"), primary_key=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Dimension-less column: models of different sizes coexist; a trigger checks that the
    # vector length matches the model. Per-model ANN indexes come with retrieval (CP4).
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    __table_args__ = (CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_emb_hash"),)


# --- Ingestion jobs ---------------------------------------------------------------------


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=JobState.QUEUED.value
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_version_id", "workspace_id"],
            ["document_versions.id", "document_versions.workspace_id"],
            ondelete="CASCADE",
            name="fk_jobs_version",
        ),
        UniqueConstraint("document_version_id", name="uq_jobs_version"),
        CheckConstraint(_in("state", JobState), name="ck_jobs_state"),
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="ck_jobs_attempts"),
        Index(
            "ix_jobs_claimable",
            "created_at",
            postgresql_where=text("state IN ('queued', 'running')"),
        ),
    )


# --- Pinned investigation scopes --------------------------------------------------------


class ScopeSnapshot(Base):
    """The exact set of document versions an investigation may read, fixed at creation.
    Investigations (CP6) reference a snapshot; retrieval filters by its versions."""

    __tablename__ = "scope_snapshots"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    release_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=SnapshotState.OPEN.value
    )
    created_at: Mapped[datetime] = _created_at()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["product_id", "workspace_id"],
            ["products.id", "products.workspace_id"],
            ondelete="CASCADE",
            name="fk_snapshots_product",
        ),
        CheckConstraint(_in("state", SnapshotState), name="ck_snapshots_state"),
        CheckConstraint("cardinality(release_ids) BETWEEN 1 AND 2", name="ck_snapshots_releases"),
        UniqueConstraint("id", "workspace_id", name="uq_snapshots_id_workspace"),
    )


class ScopeSnapshotVersion(Base):
    __tablename__ = "scope_snapshot_versions"
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "workspace_id"],
            ["scope_snapshots.id", "scope_snapshots.workspace_id"],
            ondelete="CASCADE",
            name="fk_snapshot_versions_snapshot",
        ),
        # NO ACTION (not RESTRICT): a version row cannot be hard-deleted while a snapshot
        # names it, but deleting a whole workspace still cascades, because NO ACTION is
        # checked at the end of the statement, after the snapshot rows are gone too.
        # Purge keeps the version row as a tombstone, so this only blocks hard deletes.
        ForeignKeyConstraint(
            ["document_version_id", "workspace_id"],
            ["document_versions.id", "document_versions.workspace_id"],
            name="fk_snapshot_versions_version",
        ),
        Index("ix_snapshot_versions_version", "document_version_id"),
    )


# Tables carrying a workspace_id column (all RLS-protected, as is `workspaces` itself).
TENANT_TABLES = (
    "memberships",
    "products",
    "releases",
    "documents",
    "document_versions",
    "sections",
    "chunks",
    "embeddings",
    "ingestion_jobs",
    "scope_snapshots",
    "scope_snapshot_versions",
)
GLOBAL_TABLES = ("users", "embedding_models")
