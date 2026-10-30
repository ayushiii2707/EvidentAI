"""Ingestion: validate -> parse -> structure -> chunk. Pure functions; no database or models."""

from evidentai.ingestion.chunk import chunk_document
from evidentai.ingestion.models import Chunk, MediaType, ParsedDocument
from evidentai.ingestion.parse_markdown import parse_markdown as _parse_markdown
from evidentai.ingestion.parse_pdf import parse_pdf as _parse_pdf
from evidentai.ingestion.validate import DEFAULT_LIMITS, ValidationLimits, validate

__all__ = ["Chunk", "ParsedDocument", "ValidationLimits", "chunk_document", "parse_document"]


def parse_document(
    filename: str, data: bytes, limits: ValidationLimits = DEFAULT_LIMITS
) -> ParsedDocument:
    media_type = validate(filename, data, limits)
    if media_type is MediaType.PDF:
        return _parse_pdf(filename, data)
    return _parse_markdown(filename, data)
