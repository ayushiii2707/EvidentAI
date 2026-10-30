"""Render the synthetic Markdown corpus to PDF fixtures.

Output PDFs carry an outline (bookmarks) built from the rendered heading positions,
plus a running header and a page-numbered footer, so the PDF parser is exercised on
outline matching and header/footer removal. These are controlled fixtures: they say
nothing about how the parser handles arbitrary real-world PDFs.

Usage: uv run python scripts/render_pdfs.py [corpus_dir] [out_dir]
"""

import sys
from pathlib import Path

import pymupdf

from evidentai.ingestion.parse_markdown import markdown_engine

CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.35; }
h1 { font-size: 20pt; margin-bottom: 10pt; }
h2 { font-size: 15pt; margin-top: 14pt; }
h3 { font-size: 12.5pt; margin-top: 10pt; }
code, pre { font-family: monospace; font-size: 9pt; }
blockquote { font-style: italic; margin-left: 12pt; }
table { border-collapse: collapse; margin: 6pt 0; }
th, td { border: 0.5pt solid black; padding: 2pt 4pt; font-size: 9.5pt; }
"""

PAGE = pymupdf.paper_rect("letter")
CONTENT = PAGE + (72, 72, -72, -72)


def render(md_path: Path, pdf_path: Path, header: str) -> int:
    html = markdown_engine().render(md_path.read_text("utf-8"))
    story = pymupdf.Story(html=html, user_css=CSS)
    headings: list[list] = []

    def record(pos) -> None:
        if pos.heading and pos.open_close & 1:
            headings.append([pos.heading, pos.text.strip(), pos.page_num + 1])

    tmp = pdf_path.with_suffix(".tmp.pdf")
    writer = pymupdf.DocumentWriter(str(tmp))
    more, page_num = 1, 0
    while more:
        device = writer.begin_page(PAGE)
        more, _ = story.place(CONTENT)
        story.element_positions(record, {"page_num": page_num})
        story.draw(device)
        writer.end_page()
        page_num += 1
    writer.close()

    doc = pymupdf.open(str(tmp))
    total = doc.page_count
    for i, page in enumerate(doc):
        page.insert_text((72, 40), header, fontsize=8, fontname="helv")
        page.insert_text(
            (72, PAGE.height - 36), f"Page {i + 1} of {total}", fontsize=8, fontname="helv"
        )
    doc.set_toc(headings)
    doc.set_metadata({"title": headings[0][1] if headings else md_path.stem})
    doc.save(str(pdf_path), garbage=3, deflate=True)
    doc.close()
    tmp.unlink()
    return total


def main(corpus_dir: Path, out_dir: Path) -> None:
    for md_path in sorted(corpus_dir.glob("v*/*.md")):
        version = md_path.parent.name
        pdf_path = out_dir / version / f"{md_path.stem}.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        header = f"AcmePay {version} documentation - SYNTHETIC BENCHMARK"
        pages = render(md_path, pdf_path, header)
        print(f"{pdf_path}  pages={pages}")


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    corpus = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "corpus" / "acmepay"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else corpus / "build"
    main(corpus, out)
