"""Section-bounded chunking.

Rules:
- A chunk never spans two sections, so every chunk has exactly one heading path.
- Blocks are packed whole until `max_words`; a single oversized block is split on
  sentence boundaries (prose) or line boundaries (code, tables).
- `embed_text` prefixes the heading path so a chunk keeps its section context when
  embedded on its own.

Sizes are in whitespace-separated words, not model tokens. The embedding model's
tokenizer is introduced in CP3; the limit will be re-checked against it there.
"""

import re

from evidentai.ingestion.models import (
    Block,
    BlockKind,
    Chunk,
    ParsedDocument,
    Section,
    sha256_hex,
    stable_key,
)

DEFAULT_MAX_WORDS = 300
PATH_SEPARATOR = " > "
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z`(])")


def word_count(text: str) -> int:
    return len(text.split())


def chunk_document(doc: ParsedDocument, max_words: int = DEFAULT_MAX_WORDS) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in doc.sections:
        chunks.extend(_chunk_section(doc.source_sha256, section, max_words))
    return chunks


def _chunk_section(source_sha: str, section: Section, max_words: int) -> list[Chunk]:
    pieces: list[Block] = []
    for block in section.blocks:
        pieces.extend(_split_block(block, max_words))

    groups: list[list[Block]] = []
    current: list[Block] = []
    current_words = 0
    for piece in pieces:
        n = word_count(piece.text)
        if current and current_words + n > max_words:
            groups.append(current)
            current, current_words = [], 0
        current.append(piece)
        current_words += n
    if current:
        groups.append(current)

    return [_make_chunk(source_sha, section, i, group) for i, group in enumerate(groups)]


def _split_block(block: Block, max_words: int) -> list[Block]:
    if word_count(block.text) <= max_words:
        return [block]
    if block.kind in (BlockKind.CODE, BlockKind.TABLE):
        units, joiner = block.text.split("\n"), "\n"
    else:
        units, joiner = _SENTENCE_END.split(block.text), " "

    parts: list[str] = []
    current: list[str] = []
    for unit in units:
        if current and word_count(joiner.join(current + [unit])) > max_words:
            parts.append(joiner.join(current))
            current = []
        current.append(unit)
    if current:
        parts.append(joiner.join(current))
    # A single sentence or line longer than max_words is kept whole rather than cut mid-text.
    return [block.model_copy(update={"text": p}) for p in parts]


def _make_chunk(source_sha: str, section: Section, ordinal: int, blocks: list[Block]) -> Chunk:
    text = "\n\n".join(b.text for b in blocks)
    path = section.heading_path
    embed_text = f"{PATH_SEPARATOR.join(path)}\n\n{text}" if path else text
    pages = [b.provenance.page for b in blocks if b.provenance.page is not None]
    lines = [b.provenance.line for b in blocks if b.provenance.line is not None]
    return Chunk(
        key=stable_key(source_sha, "chunk", section.key, ordinal),
        section_key=section.key,
        heading_path=path,
        ordinal=ordinal,
        text=text,
        embed_text=embed_text,
        content_hash=sha256_hex(embed_text),
        word_count=word_count(text),
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        line_start=min(lines) if lines else None,
        line_end=max(lines) if lines else None,
    )
