"""Typed ingestion errors. Each carries a stable `code` that the API and job table expose."""


class IngestionError(Exception):
    code = "ingestion_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class UnsupportedFileType(IngestionError):
    code = "unsupported_file_type"


class FileTooLarge(IngestionError):
    code = "file_too_large"


class EmptyFile(IngestionError):
    code = "empty_file"


class CorruptFile(IngestionError):
    code = "corrupt_file"


class EncryptedPdf(IngestionError):
    code = "encrypted_pdf"


class TooManyPages(IngestionError):
    code = "too_many_pages"


class NoExtractableText(IngestionError):
    """The document has no text layer (e.g. a scanned PDF). OCR is out of scope."""

    code = "no_extractable_text"
