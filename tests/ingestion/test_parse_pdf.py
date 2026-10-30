"""Controlled fixture tests: PDFs are generated from the synthetic Markdown corpus.

Passing these shows the parser recovers structure and text from cleanly generated PDFs.
It does not show robustness on arbitrary real-world PDFs; that is measured in Phase 2.
"""

import re

import pymupdf
import pytest

from evidentai.ingestion import parse_document
from evidentai.ingestion.models import BlockKind
from tests.conftest import DOC_NAMES, VERSIONS

MULTI_PAGE = [(v, n) for v in VERSIONS for n in ("auth-guide", "api-reference")]
ALL_DOCS = [(v, n) for v in VERSIONS for n in DOC_NAMES]


def words(text: str) -> list[str]:
    return re.findall(r"\S+", text.replace("|", " "))


def test_fixtures_are_multi_page(pdf_docs):
    for key in MULTI_PAGE:
        assert pdf_docs[key].page_count >= 2, key


@pytest.mark.parametrize("key", MULTI_PAGE)
def test_pdf_section_tree_matches_markdown(key, md_docs, pdf_docs):
    md, pdf = md_docs[key], pdf_docs[key]
    assert pdf.parser == "pymupdf/outline"
    assert pdf.warnings == []
    assert [s.heading_path for s in pdf.sections] == [s.heading_path for s in md.sections]
    assert [s.level for s in pdf.sections] == [s.level for s in md.sections]


@pytest.mark.parametrize("key", ALL_DOCS)
def test_pdf_section_text_matches_markdown(key, md_docs, pdf_docs):
    md, pdf = md_docs[key], pdf_docs[key]
    for section in md.sections:
        pdf_section = pdf.section_by_path(section.heading_path)
        assert pdf_section is not None, section.heading_path
        assert words(pdf_section.text) == words(section.text), section.heading_path


@pytest.mark.xfail(
    strict=True,
    reason="known limit: one-page PDFs give no repetition signal, "
    "so the running header is kept as a preamble section",
)
@pytest.mark.parametrize("version", VERSIONS)
def test_single_page_running_header_removed(version, md_docs, pdf_docs):
    key = (version, "release-notes")
    assert pdf_docs[key].page_count == 1
    assert [s.heading_path for s in pdf_docs[key].sections] == [
        s.heading_path for s in md_docs[key].sections
    ]


@pytest.mark.parametrize("key", ALL_DOCS)
def test_every_block_has_page_provenance(key, pdf_docs):
    doc = pdf_docs[key]
    for s in doc.sections:
        assert 1 <= s.heading_provenance.page <= doc.page_count
        for b in s.blocks:
            assert b.provenance.page is not None and 1 <= b.provenance.page <= doc.page_count


@pytest.mark.parametrize("key", ALL_DOCS)
def test_page_numbers_removed(key, pdf_docs):
    for s in pdf_docs[key].sections:
        for b in s.blocks:
            assert not re.search(r"Page \d+ of \d+", b.text), (s.heading_path, b.text)


@pytest.mark.parametrize("key", MULTI_PAGE)
def test_running_header_removed_on_multi_page(key, pdf_docs):
    for s in pdf_docs[key].sections:
        for b in s.blocks:
            assert "documentation - SYNTHETIC BENCHMARK" not in b.text


def test_table_split_across_pages_keeps_all_rows(pdf_docs):
    doc = pdf_docs[("3.0", "auth-guide")]
    section = doc.section_by_path(["AcmePay Authentication Guide (v3.0)", "Error Handling"])
    tables = [b for b in section.blocks if b.kind is BlockKind.TABLE]
    assert {b.provenance.page for b in tables} == {2, 3}, "fixture should split this table"
    rows = [row for t in tables for row in t.text.splitlines()]
    assert "401 | api_keys_unsupported | A secret API key was sent to the v3 API" in rows
    assert "401 | invalid_token | The access token is malformed or was revoked" in rows
    assert len(rows) == 7  # header + 6 error codes


def test_code_block_keeps_lines(pdf_docs):
    doc = pdf_docs[("3.0", "api-reference")]
    section = doc.section_by_path(["AcmePay API Reference (v3.0)", "Payments", "Create a Payment"])
    code = [b for b in section.blocks if b.kind is BlockKind.CODE and "curl" in b.text]
    assert len(code) == 1
    assert code[0].text.splitlines()[2].strip() == '-H "Idempotency-Key: 5c1f0a3e-order-1042" \\'


@pytest.mark.parametrize("key", MULTI_PAGE)
def test_font_size_fallback_without_outline(key, pdf_dir, md_docs):
    version, name = key
    with pymupdf.open(pdf_dir / f"v{version}" / f"{name}.pdf") as doc:
        doc.set_toc([])
        data = doc.tobytes()
    parsed = parse_document(f"{name}.pdf", data)
    assert parsed.parser == "pymupdf/font-size"
    assert [s.heading_path for s in parsed.sections] == [
        s.heading_path for s in md_docs[key].sections
    ]


def test_outline_entry_missing_from_text_is_reported(pdf_dir):
    with pymupdf.open(pdf_dir / "v3.0" / "auth-guide.pdf") as doc:
        toc = doc.get_toc()
        toc.insert(2, [2, "A Heading That Does Not Exist", 1])
        doc.set_toc(toc)
        data = doc.tobytes()
    parsed = parse_document("auth-guide.pdf", data)
    assert any("A Heading That Does Not Exist" in w for w in parsed.warnings)
    assert len(parsed.sections) == 14  # the real headings are still found
