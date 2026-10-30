import pymupdf
import pytest

from evidentai.ingestion import ValidationLimits, parse_document
from evidentai.ingestion.errors import (
    CorruptFile,
    EmptyFile,
    EncryptedPdf,
    FileTooLarge,
    NoExtractableText,
    TooManyPages,
    UnsupportedFileType,
)
from evidentai.ingestion.models import MediaType
from evidentai.ingestion.validate import validate


def make_pdf(pages: list[str], **save_kwargs) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = doc.tobytes(**save_kwargs)
    doc.close()
    return data


def test_accepts_pdf_and_markdown():
    assert validate("a.pdf", make_pdf(["hello"])) is MediaType.PDF
    assert validate("a.md", b"# Title\n\ntext") is MediaType.MARKDOWN


def test_pdf_detected_by_content_not_suffix():
    assert validate("upload.bin", make_pdf(["hello"])) is MediaType.PDF


@pytest.mark.parametrize(
    ("filename", "data", "error"),
    [
        ("a.pdf", b"", EmptyFile),
        ("a.md", b"   \n  ", EmptyFile),
        ("a.txt", b"plain text", UnsupportedFileType),
        ("a.pdf", b"not a pdf at all", CorruptFile),
        ("a.pdf", b"%PDF-1.7\n garbage that is not a pdf body", CorruptFile),
        ("a.md", b"# Title\x00\n", CorruptFile),
        ("a.md", "# Titel ä".encode("latin-1"), CorruptFile),
    ],
)
def test_rejects_bad_input(filename, data, error):
    with pytest.raises(error) as exc:
        validate(filename, data)
    assert exc.value.code == error.code


def test_rejects_encrypted_pdf():
    data = make_pdf(["secret"], encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="o")
    with pytest.raises(EncryptedPdf):
        validate("a.pdf", data)


def test_rejects_pdf_without_text_layer():
    with pytest.raises(NoExtractableText):
        validate("scan.pdf", make_pdf(["", ""]))


def test_size_and_page_limits():
    data = make_pdf(["one", "two"])
    with pytest.raises(FileTooLarge):
        validate("a.pdf", data, ValidationLimits(max_bytes=len(data) - 1))
    with pytest.raises(TooManyPages):
        validate("a.pdf", data, ValidationLimits(max_pages=1))


def test_parse_document_validates_first():
    with pytest.raises(UnsupportedFileType):
        parse_document("a.docx", b"PK\x03\x04")
