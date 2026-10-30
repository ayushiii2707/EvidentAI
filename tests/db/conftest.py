"""Database fixtures. Tests run against a disposable `*_test` database that is dropped and
recreated per session; destructive helpers refuse anything else (evidentai/db/safety.py).

Requires the local Postgres: `docker compose up -d db`.
"""

import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, insert, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

from evidentai.config import APP_ROLE, app_database_url, test_database_url
from evidentai.db.lifecycle import ALLOWED_TRANSITIONS
from evidentai.db.models import (
    GLOBAL_TABLES,
    TENANT_TABLES,
    DocumentKind,
    EmbeddingModel,
    IngestionJob,
    User,
    VersionStatus,
)
from evidentai.db.repository import WorkspaceRepository
from evidentai.db.safety import recreate_database, truncate_all
from evidentai.db.scope import create_workspace, set_workspace, workspace_transaction
from evidentai.ingestion import chunk_document, parse_document

ROOT = Path(__file__).resolve().parents[2]


def alembic_config(url: URL) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["url"] = url
    return cfg


def app_url_for(test_url: URL) -> URL:
    return app_database_url().set(database=test_url.database)


@pytest.fixture(scope="session")
def test_url() -> URL:
    url = test_database_url()
    try:
        recreate_database(url)
    except OperationalError as exc:
        pytest.fail(
            f"cannot reach test Postgres at {url.host}:{url.port} "
            f"(run `docker compose up -d db`): {exc.orig}",
            pytrace=False,
        )
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="session")
def owner_engine(test_url) -> Engine:
    engine = create_engine(test_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(test_url) -> Engine:
    engine = create_engine(app_url_for(test_url))
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT current_user")) == APP_ROLE
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(owner_engine):
    yield
    with owner_engine.begin() as conn:
        truncate_all(conn, ["workspaces", *TENANT_TABLES, *GLOBAL_TABLES])


# --- seeding ------------------------------------------------------------------------------

SAMPLE_MD = b"""# Guide (v1)

Intro text.

## Auth

Use OAuth tokens.

## Limits

50 requests per second.
"""


def path_to(target: VersionStatus) -> list[VersionStatus]:
    """Shortest allowed status path from pending to `target`."""
    start = VersionStatus.PENDING
    prev: dict[VersionStatus, VersionStatus | None] = {start: None}
    queue = deque([start])
    while queue:
        s = queue.popleft()
        for nxt in ALLOWED_TRANSITIONS[s]:
            if nxt not in prev:
                prev[nxt] = s
                queue.append(nxt)
    path, node = [], target
    while node is not None:
        path.append(node)
        node = prev[node]
    return list(reversed(path))


def walk_to(repo: WorkspaceRepository, version_id: uuid.UUID, target: VersionStatus) -> None:
    steps = path_to(target)
    for a, b in zip(steps, steps[1:], strict=False):
        if b is VersionStatus.ACTIVE:
            repo.activate(version_id)
        else:
            repo.transition(version_id, a, b)


def create_user(owner_engine: Engine, email: str) -> uuid.UUID:
    # The application role has no grant on users (no CP2/CP3 path needs it).
    with owner_engine.begin() as conn:
        return conn.scalar(insert(User.__table__).values(email=email).returning(User.id))


def embedding_model(owner_engine: Engine, dimensions: int = 4) -> int:
    with owner_engine.begin() as conn:
        return conn.scalar(
            insert(EmbeddingModel.__table__)
            .values(
                name="test-model", revision="r1", dimensions=dimensions, normalization_version="n1"
            )
            .returning(EmbeddingModel.id)
        )


@dataclass
class Seeded:
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    product_id: uuid.UUID
    release_ids: list[uuid.UUID]
    document_id: uuid.UUID
    version_id: uuid.UUID
    snapshot_id: uuid.UUID
    extra: dict = field(default_factory=dict)


def seed_workspace(app_engine: Engine, owner_engine: Engine, label: str, model_id: int) -> Seeded:
    """A workspace with a row in every tenant table: an active, indexed version with
    content, an embedding, an ingestion job and an open snapshot."""
    user_id = create_user(owner_engine, f"{label}@example.test")
    ws = create_workspace(app_engine, user_id, f"workspace {label}")
    parsed = parse_document("guide.md", SAMPLE_MD)
    parsed_chunks = chunk_document(parsed)
    with workspace_transaction(app_engine, user_id, ws) as repo:
        product = repo.create_product(f"Product {label}")
        r1 = repo.create_release(product, "1.0")
        r2 = repo.create_release(product, "2.0")
        doc = repo.create_document(product, "guide", DocumentKind.GUIDE)
        v = repo.create_version(
            doc,
            r1,
            source_sha256=parsed.source_sha256,
            filename="guide.md",
            media_type="text/markdown",
            byte_size=len(SAMPLE_MD),
            storage_key=f"{label}/guide.md",
        ).id
        walk_to(repo, v, VersionStatus.INDEXING)
        repo.write_content(v, parsed, parsed_chunks)
        repo.activate(v)
        repo.conn.execute(
            insert(IngestionJob.__table__).values(workspace_id=ws, document_version_id=v)
        )
        repo.conn.execute(
            text(
                "INSERT INTO embeddings (workspace_id, model_id, content_hash, embedding) "
                "VALUES (:ws, :m, :h, '[1,2,3,4]')"
            ),
            {"ws": ws, "m": model_id, "h": parsed_chunks[0].content_hash},
        )
        snapshot = repo.create_snapshot(product, [r1])
    return Seeded(user_id, ws, product, [r1, r2], doc, v, snapshot)


@pytest.fixture
def two_workspaces(app_engine, owner_engine) -> tuple[Seeded, Seeded]:
    model_id = embedding_model(owner_engine)
    a = seed_workspace(app_engine, owner_engine, "a", model_id)
    b = seed_workspace(app_engine, owner_engine, "b", model_id)
    a.extra["model_id"] = b.extra["model_id"] = model_id
    return a, b


__all__ = ["set_workspace"]
