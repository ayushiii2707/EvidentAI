import re

import pytest

from evidentai.ingestion import chunk_document
from evidentai.ingestion.chunk import PATH_SEPARATOR
from tests.conftest import DOC_NAMES, VERSIONS, md_path, parse_file

ALL_DOCS = [(v, n) for v in VERSIONS for n in DOC_NAMES]


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@pytest.fixture(params=["md", "pdf"])
def docs(request, md_docs, pdf_docs):
    return md_docs if request.param == "md" else pdf_docs


@pytest.mark.parametrize("max_words", [300, 40, 12])
@pytest.mark.parametrize("key", ALL_DOCS)
def test_chunks_stay_inside_one_section_and_lose_nothing(key, max_words, docs):
    doc = docs[key]
    chunks = chunk_document(doc, max_words=max_words)
    sections = {s.key: s for s in doc.sections}
    for c in chunks:
        section = sections[c.section_key]
        assert c.heading_path == section.heading_path
        assert norm(c.text) in norm(section.text)
    for s in doc.sections:
        joined = " ".join(c.text for c in chunks if c.section_key == s.key)
        assert norm(joined).split() == norm(s.text).split(), s.heading_path


@pytest.mark.parametrize("key", ALL_DOCS)
def test_size_limit_respected_except_unsplittable_units(key, docs):
    max_words = 40
    for c in chunk_document(docs[key], max_words=max_words):
        if c.word_count > max_words:
            # Allowed only when a single sentence or line is itself longer than the limit.
            assert "\n" not in c.text and len(re.split(r"(?<=[.!?])\s+", c.text)) == 1


def test_provenance(md_docs, pdf_docs):
    for c in chunk_document(pdf_docs[("3.0", "auth-guide")]):
        assert c.page_start is not None and c.page_start <= c.page_end
        assert c.line_start is None
    for c in chunk_document(md_docs[("3.0", "auth-guide")]):
        assert c.line_start is not None and c.line_start <= c.line_end
        assert c.page_start is None


def test_embed_text_carries_heading_path(md_docs):
    for c in chunk_document(md_docs[("3.0", "auth-guide")]):
        if c.heading_path:
            assert c.embed_text.startswith(PATH_SEPARATOR.join(c.heading_path) + "\n\n")
            assert c.embed_text.endswith(c.text)


def test_keys_are_deterministic_and_unique():
    path = md_path("3.0", "api-reference")
    first = chunk_document(parse_file(path))
    second = chunk_document(parse_file(path))
    assert [c.key for c in first] == [c.key for c in second]
    assert len({c.key for c in first}) == len(first)


def test_section_without_body_produces_no_chunk(md_docs):
    doc = md_docs[("3.0", "auth-guide")]
    empty = doc.section_by_path(["AcmePay Authentication Guide (v3.0)", "Troubleshooting"])
    assert empty.blocks == []
    assert not [c for c in chunk_document(doc) if c.section_key == empty.key]
