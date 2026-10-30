"""File validation: decide whether bytes are a supported, safe, parseable document."""

from dataclasses import dataclass
from pathlib import PurePath

import pymupdf

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

MAX_BYTES = 50 * 1024 * 1024
MAX_PAGES = 2000
MARKDOWN_SUFFIXES = {".md", ".markdown"}


@dataclass(frozen=True)
class ValidationLimits:
    max_bytes: int = MAX_BYTES
    max_pages: int = MAX_PAGES


DEFAULT_LIMITS = ValidationLimits()


def detect_media_type(filename: str, data: bytes) -> MediaType:
    """Content decides for PDF; Markdown has no magic bytes, so it needs the suffix too."""
    suffix = PurePath(filename).suffix.lower()
    if data.startswith(b"%PDF-"):
        return MediaType.PDF
    if suffix == ".pdf":
        raise CorruptFile(f"{filename}: has .pdf suffix but no PDF header")
    if suffix in MARKDOWN_SUFFIXES:
        return MediaType.MARKDOWN
    raise UnsupportedFileType(f"{filename}: only PDF and Markdown are supported")


def validate(filename: str, data: bytes, limits: ValidationLimits = DEFAULT_LIMITS) -> MediaType:
    if not data:
        raise EmptyFile(f"{filename}: file is empty")
    if len(data) > limits.max_bytes:
        raise FileTooLarge(f"{filename}: {len(data)} bytes exceeds limit of {limits.max_bytes}")

    media_type = detect_media_type(filename, data)
    if media_type is MediaType.PDF:
        _validate_pdf(filename, data, limits)
    else:
        _validate_markdown(filename, data)
    return media_type


def _validate_pdf(filename: str, data: bytes, limits: ValidationLimits) -> None:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # pymupdf raises several internal types for broken files
        raise CorruptFile(f"{filename}: cannot be opened as PDF ({exc})") from exc
    with doc:
        if doc.needs_pass:
            raise EncryptedPdf(f"{filename}: password-protected PDFs are not supported")
        if doc.page_count == 0:
            raise CorruptFile(f"{filename}: PDF has no pages")
        if doc.page_count > limits.max_pages:
            raise TooManyPages(
                f"{filename}: {doc.page_count} pages exceeds limit of {limits.max_pages}"
            )
        if not any(page.get_text("text").strip() for page in doc):
            raise NoExtractableText(f"{filename}: no text layer found (scanned PDFs need OCR)")


def _validate_markdown(filename: str, data: bytes) -> None:
    if b"\x00" in data:
        raise CorruptFile(f"{filename}: Markdown file contains NUL bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CorruptFile(f"{filename}: Markdown must be UTF-8 ({exc})") from exc
    if not text.strip():
        raise EmptyFile(f"{filename}: file has no text")
