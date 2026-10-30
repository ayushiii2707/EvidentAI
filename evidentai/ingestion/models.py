"""Normalized document model shared by every parser.

Parsers turn bytes into a `ParsedDocument`: an ordered list of sections, each holding
ordered blocks of text with provenance. Chunking and indexing only ever see this model,
so they never depend on the source format.
"""

import hashlib
import re
import unicodedata
from enum import StrEnum

from pydantic import BaseModel, Field


class MediaType(StrEnum):
    PDF = "application/pdf"
    MARKDOWN = "text/markdown"


class BlockKind(StrEnum):
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    CODE = "code"
    TABLE = "table"


class Provenance(BaseModel):
    """Where a piece of text came from. PDFs have pages (1-based); Markdown has lines (1-based)."""

    page: int | None = None
    line: int | None = None


class Block(BaseModel):
    kind: BlockKind
    text: str
    provenance: Provenance


class Section(BaseModel):
    key: str
    parent_key: str | None
    level: int  # 0 = preamble before the first heading, 1 = H1, ...
    heading: str
    heading_path: list[str]
    ordinal: int  # position in document order
    blocks: list[Block] = Field(default_factory=list)
    heading_provenance: Provenance

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)


class ParsedDocument(BaseModel):
    source_sha256: str
    filename: str
    media_type: MediaType
    parser: str
    parser_version: str
    page_count: int | None
    title: str | None
    sections: list[Section]
    warnings: list[str] = Field(default_factory=list)

    def section_by_path(self, path: list[str]) -> Section | None:
        for s in self.sections:
            if s.heading_path == path:
                return s
        return None


class Chunk(BaseModel):
    key: str
    section_key: str
    heading_path: list[str]
    ordinal: int  # position within its section
    text: str
    embed_text: str  # heading path (without document title) + text; what the embedder sees
    content_hash: str  # embed_content_hash(embed_text); with the model id, the embedding key
    word_count: int
    page_start: int | None
    page_end: int | None
    line_start: int | None
    line_end: int | None


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def stable_key(*parts: object, length: int = 24) -> str:
    """Deterministic identifier from ordered parts. Unit separator avoids ambiguous joins."""
    return sha256_hex("\x1f".join(str(p) for p in parts))[:length]


# Bump when normalization changes: old hashes then stop matching, so stale embeddings are
# never reused for text that would now normalize differently.
EMBED_NORMALIZATION_VERSION = "n1"


def normalize_for_embedding(text: str) -> str:
    """Unicode NFKC, whitespace collapsed. Case and punctuation are kept: they carry meaning
    in technical text (`capture` vs `Capture`, `/v2/` vs `/v3/`)."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def embed_content_hash(text: str) -> str:
    """Key for embedding reuse. Depends only on normalized content, never on which document,
    version or workspace the text came from; provenance lives on the chunk row instead."""
    return sha256_hex(f"{EMBED_NORMALIZATION_VERSION}\x1f{normalize_for_embedding(text)}")
