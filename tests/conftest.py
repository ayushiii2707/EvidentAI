import importlib.util
from pathlib import Path

import pytest

from evidentai.ingestion import ParsedDocument, parse_document

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus" / "acmepay"
VERSIONS = ("2.4", "3.0")
DOC_NAMES = ("auth-guide", "api-reference", "release-notes")


def _load_renderer():
    spec = importlib.util.spec_from_file_location(
        "render_pdfs", ROOT / "scripts" / "render_pdfs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def pdf_dir(tmp_path_factory) -> Path:
    """Render the synthetic corpus to PDFs once per test session (controlled fixtures)."""
    out = tmp_path_factory.mktemp("pdf")
    _load_renderer().main(CORPUS, out)
    return out


def md_path(version: str, name: str) -> Path:
    return CORPUS / f"v{version}" / f"{name}.md"


def parse_file(path: Path) -> ParsedDocument:
    return parse_document(path.name, path.read_bytes())


@pytest.fixture(scope="session")
def md_docs() -> dict[tuple[str, str], ParsedDocument]:
    return {(v, n): parse_file(md_path(v, n)) for v in VERSIONS for n in DOC_NAMES}


@pytest.fixture(scope="session")
def pdf_docs(pdf_dir) -> dict[tuple[str, str], ParsedDocument]:
    return {(v, n): parse_file(pdf_dir / f"v{v}" / f"{n}.pdf") for v in VERSIONS for n in DOC_NAMES}
