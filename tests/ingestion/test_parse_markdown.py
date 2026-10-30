from evidentai.ingestion import parse_document
from evidentai.ingestion.models import BlockKind

TITLE = "AcmePay Authentication Guide (v3.0)"


def test_section_tree(md_docs):
    doc = md_docs[("3.0", "auth-guide")]
    assert doc.title == TITLE
    paths = [s.heading_path for s in doc.sections]
    assert paths[:6] == [
        [TITLE],
        [TITLE, "Overview"],
        [TITLE, "OAuth Client Credentials"],
        [TITLE, "OAuth Client Credentials", "Creating OAuth Clients"],
        [TITLE, "OAuth Client Credentials", "Requesting Access Tokens"],
        [TITLE, "OAuth Client Credentials", "Token Expiration and Renewal"],
    ]
    assert [TITLE, "Troubleshooting", "Webhook Signature Mismatch"] in paths


def test_parent_keys_follow_heading_levels(md_docs):
    doc = md_docs[("3.0", "auth-guide")]
    by_key = {s.key: s for s in doc.sections}
    for s in doc.sections:
        if s.parent_key is None:
            assert s.level <= 1
        else:
            parent = by_key[s.parent_key]
            assert parent.level < s.level
            assert s.heading_path[:-1] == parent.heading_path


def test_block_kinds_and_line_provenance(md_docs):
    doc = md_docs[("3.0", "auth-guide")]
    tokens = doc.section_by_path([TITLE, "OAuth Client Credentials", "Requesting Access Tokens"])
    kinds = [b.kind for b in tokens.blocks]
    assert BlockKind.CODE in kinds
    code = next(b for b in tokens.blocks if b.kind is BlockKind.CODE)
    assert "grant_type=client_credentials" in code.text
    assert "\n" in code.text  # code keeps its line structure
    assert all(b.provenance.line and b.provenance.page is None for b in tokens.blocks)

    errors = doc.section_by_path([TITLE, "Error Handling"])
    table = next(b for b in errors.blocks if b.kind is BlockKind.TABLE)
    assert "401 | token_expired | The access token has expired" in table.text.splitlines()


def test_inline_markup_is_stripped(md_docs):
    doc = md_docs[("3.0", "api-reference")]
    section = doc.section_by_path(["AcmePay API Reference (v3.0)", "Payments", "Create a Payment"])
    assert section.blocks[0].text == "POST /payments"


def test_preamble_and_duplicate_headings():
    data = b"Intro before any heading.\n\n# A\n\n## Errors\n\nx\n\n# B\n\n## Errors\n\ny\n\n# A\n"
    doc = parse_document("dup.md", data)
    assert doc.sections[0].level == 0 and doc.sections[0].heading_path == []
    paths = [s.heading_path for s in doc.sections]
    assert paths.count(["A"]) == 2
    keys = [s.key for s in doc.sections]
    assert len(keys) == len(set(keys)), "repeated heading paths must still get unique keys"
