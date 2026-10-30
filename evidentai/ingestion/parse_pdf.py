"""PDF parser built on PyMuPDF text extraction.

Strategy, in order:
1. Extract text blocks with font size, monospace flag, bbox and page. Text is NFKC
   normalized (ligatures such as "fi" become plain letters).
2. Detect tables; read each cell from its own rectangle. A table row cut off by a page
   break often loses its ruling line and is missed by detection, so a text block that
   touches a table edge and fits inside its columns is re-attached as a row.
3. Merge consecutive prose blocks separated by less than a line gap: PyMuPDF starts a
   new block when a line begins with a font change (e.g. inline code).
4. Drop running headers/footers: short lines in the top/bottom page margin whose
   digit-normalized text repeats on at least half of the pages.
5. Find headings: match the PDF outline (bookmarks) when present; otherwise infer
   heading levels from font sizes larger than the dominant body size.

Known limits (not yet measured on real PDFs): multi-column layouts, headings that
are bold but not larger than body text, tables without ruling lines, scanned pages.
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

import pymupdf

from evidentai.ingestion.models import (
    Block,
    BlockKind,
    MediaType,
    ParsedDocument,
    Provenance,
    sha256_hex,
)
from evidentai.ingestion.structure import SectionBuilder

PARSER_NAME = "pymupdf"
PARSER_VERSION = "1"

pymupdf.no_recommend_layout()  # otherwise find_tables() prints an advert to stdout

MONOSPACE_FLAG = 8
MARGIN_FRACTION = 0.08
HEADING_SIZE_DELTA = 1.0  # points above body size to count as a heading
MAX_HEADING_CHARS = 150
ORPHAN_ROW_GAP = 8.0  # points between a stray row and the table edge it belongs to
PARAGRAPH_GAP_FACTOR = 0.6  # gaps below this fraction of font size continue a paragraph
BULLETS = ("•", "◦", "▪", "‣", "–")
PAGE_NUMBER = re.compile(r"(page\s+)?\d+(\s*(of|/)\s*\d+)?", re.IGNORECASE)


@dataclass
class Unit:
    """One extracted text block (or table) in reading order."""

    page: int  # 1-based
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float
    mono: bool
    is_table: bool = False


def normalize_heading(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", text).strip()


def parse_pdf(filename: str, data: bytes) -> ParsedDocument:
    source_sha = sha256_hex(data)
    warnings: list[str] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        units = _extract_units(doc)
        units = _drop_running_headers(units, doc)
        toc = doc.get_toc(simple=True)
        page_count = doc.page_count
        meta_title = (doc.metadata or {}).get("title") or None

    if toc:
        heading_levels = _match_outline(units, toc, warnings)
        strategy = "outline"
    else:
        heading_levels = _infer_heading_levels(units)
        strategy = "font-size"
        warnings.append("no PDF outline found; headings inferred from font sizes")

    builder = SectionBuilder(source_sha)
    title: str | None = None
    for idx, unit in enumerate(units):
        prov = Provenance(page=unit.page)
        level = heading_levels.get(idx)
        if level is not None:
            if level == 1 and title is None:
                title = unit.text
            builder.heading(level, unit.text, prov)
        else:
            builder.block(_to_block(unit, prov))

    return ParsedDocument(
        source_sha256=source_sha,
        filename=filename,
        media_type=MediaType.PDF,
        parser=f"{PARSER_NAME}/{strategy}",
        parser_version=PARSER_VERSION,
        page_count=page_count,
        title=title or meta_title,
        sections=builder.build(),
        warnings=warnings,
    )


def _extract_units(doc: pymupdf.Document) -> list[Unit]:
    units: list[Unit] = []
    for page_index, page in enumerate(doc):
        page_no = page_index + 1
        text_units = []
        for block in page.get_text("dict")["blocks"]:
            text_units.extend(_text_units(block, page_no))
        tables, text_units = _extract_tables(page, page_no, text_units)
        page_units = sorted(tables + text_units, key=lambda u: u.y0)
        units.extend(_merge_paragraph_continuations(page_units))
    return units


def _text_units(block: dict, page_no: int) -> list[Unit]:
    """One unit per block, except that each bulleted line starts a new unit."""
    if block.get("type") != 0:
        return []
    groups: list[list[dict]] = []
    for line in block["lines"]:
        if not any(s["text"].strip() for s in line["spans"]):
            continue
        starts_bullet = (
            _nfkc("".join(s["text"] for s in line["spans"])).lstrip().startswith(BULLETS)
        )
        if not groups or starts_bullet:
            groups.append([])
        groups[-1].append(line)
    return [_lines_to_unit(lines, page_no) for lines in groups]


def _lines_to_unit(lines: list[dict], page_no: int) -> Unit:
    spans = [s for ln in lines for s in ln["spans"] if s["text"].strip()]
    mono = all(s["flags"] & MONOSPACE_FLAG for s in spans)
    line_texts = [_nfkc("".join(s["text"] for s in ln["spans"])) for ln in lines]
    text = "\n".join(t.rstrip() for t in line_texts) if mono else _join_lines(line_texts)
    sizes = Counter()
    for s in spans:
        sizes[round(s["size"], 1)] += len(s["text"])
    rect = pymupdf.Rect()
    for ln in lines:
        rect |= pymupdf.Rect(ln["bbox"])
    return Unit(page_no, rect.x0, rect.y0, rect.x1, rect.y1, text, sizes.most_common(1)[0][0], mono)


def _extract_tables(
    page: pymupdf.Page, page_no: int, text_units: list[Unit]
) -> tuple[list[Unit], list[Unit]]:
    """Return (table units, remaining text units)."""
    table_units: list[Unit] = []
    consumed: set[int] = set()
    for table in page.find_tables().tables:
        rect = pymupdf.Rect(table.bbox)
        columns = [(c[0], c[2]) for c in table.rows[0].cells if c is not None]
        for i, u in enumerate(text_units):
            center = pymupdf.Point((u.x0 + u.x1) / 2, (u.y0 + u.y1) / 2)
            if center in rect:
                consumed.add(i)
        rows: list[tuple[float, list[str]]] = []
        for row in table.rows:
            cells = [_cell_text(page, c) for c in row.cells if c is not None]
            rows.append((row.bbox[1], cells))
        for i, u in enumerate(text_units):
            if i in consumed or u.mono:
                continue
            touches_top = 0 <= rect.y0 - u.y1 <= ORPHAN_ROW_GAP
            touches_bottom = 0 <= u.y0 - rect.y1 <= ORPHAN_ROW_GAP
            inside_columns = u.x0 >= rect.x0 - 2 and u.x1 <= rect.x1 + 2
            if (touches_top or touches_bottom) and inside_columns:
                cells = [_cell_text(page, (x0, u.y0, x1, u.y1)) for x0, x1 in columns]
                rows.append((u.y0, cells))
                rect |= pymupdf.Rect(u.x0, u.y0, u.x1, u.y1)
                consumed.add(i)
        rows.sort(key=lambda r: r[0])
        text = "\n".join(" | ".join(cells) for _, cells in rows)
        table_units.append(
            Unit(page_no, rect.x0, rect.y0, rect.x1, rect.y1, text, 0.0, False, is_table=True)
        )
    remaining = [u for i, u in enumerate(text_units) if i not in consumed]
    return table_units, remaining


def _cell_text(page: pymupdf.Page, bbox) -> str:
    # Reading the cell rectangle directly keeps characters such as "_" in order;
    # Table.extract() was observed to move underscores to the end of the cell.
    return _clean(_nfkc(page.get_textbox(pymupdf.Rect(bbox))))


def _merge_paragraph_continuations(units: list[Unit]) -> list[Unit]:
    merged: list[Unit] = []
    for u in units:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and not (prev.is_table or u.is_table or prev.mono or u.mono)
            and prev.size == u.size
            # Line boxes of consecutive lines can overlap slightly, so allow a small negative gap.
            and -PARAGRAPH_GAP_FACTOR * u.size < u.y0 - prev.y1 < PARAGRAPH_GAP_FACTOR * u.size
            and not u.text.startswith(BULLETS)
        ):
            prev.text = _join_lines([prev.text, u.text])
            prev.x0, prev.x1, prev.y1 = min(prev.x0, u.x0), max(prev.x1, u.x1), u.y1
            continue
        merged.append(u)
    return merged


def _join_lines(lines: list[str]) -> str:
    """Join wrapped lines with a space, except after "/" (a wrapped URL or path) or after a
    hyphen ending a word (a wrapped compound such as "re-serialized"). The hyphen is kept;
    soft hyphenation ("authen-tication") would be joined incorrectly and is not handled.
    """
    out = ""
    for line in (_clean(x) for x in lines):
        if not line:
            continue
        no_space = not out or out.endswith("/") or re.search(r"[A-Za-z]-$", out)
        out = f"{out}{line}" if no_space else f"{out} {line}"
    return out


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _drop_running_headers(units: list[Unit], doc: pymupdf.Document) -> list[Unit]:
    """Drop margin text that repeats across pages, and margin page numbers on any page.

    A one-page document gives no repetition signal, so a running header there is kept.
    """
    height = doc[0].rect.height
    margin = height * MARGIN_FRACTION

    def in_margin(u: Unit) -> bool:
        return not u.is_table and (u.y1 <= margin or u.y0 >= height - margin)

    def signature(u: Unit) -> str:
        return re.sub(r"\d+", "#", u.text.casefold())

    pages_with = Counter(sig for sig, _ in {(signature(u), u.page) for u in units if in_margin(u)})
    repeated = (
        {sig for sig, n in pages_with.items() if n >= max(2, doc.page_count / 2)}
        if doc.page_count >= 2
        else set()
    )
    return [
        u
        for u in units
        if not (in_margin(u) and (signature(u) in repeated or PAGE_NUMBER.fullmatch(u.text)))
    ]


def _match_outline(units: list[Unit], toc: list, warnings: list[str]) -> dict[int, int]:
    """Map outline entries to text units, in order. Unmatched entries become warnings."""
    levels: dict[int, int] = {}
    cursor = 0
    for level, title, page in toc:
        target = normalize_heading(title)
        found = None
        for idx in range(cursor, len(units)):
            u = units[idx]
            if page > 0 and u.page > page:
                break
            if not u.is_table and normalize_heading(u.text) == target:
                found = idx
                break
        if found is None:
            warnings.append(f"outline entry not found in page text: {title!r} (page {page})")
            continue
        levels[found] = level
        cursor = found + 1
    return levels


def _infer_heading_levels(units: list[Unit]) -> dict[int, int]:
    text_units = [u for u in units if not u.is_table and not u.mono]
    if not text_units:
        return {}
    weights = Counter()
    for u in text_units:
        weights[u.size] += len(u.text)
    body_size = weights.most_common(1)[0][0]
    candidates = {
        idx: u.size
        for idx, u in enumerate(units)
        if not u.is_table
        and not u.mono
        and u.size >= body_size + HEADING_SIZE_DELTA
        and len(u.text) <= MAX_HEADING_CHARS
    }
    sizes = sorted({round(s * 2) / 2 for s in candidates.values()}, reverse=True)
    rank = {s: i + 1 for i, s in enumerate(sizes)}
    return {idx: rank[round(s * 2) / 2] for idx, s in candidates.items()}


def _to_block(unit: Unit, prov: Provenance) -> Block:
    if unit.is_table:
        return Block(kind=BlockKind.TABLE, text=unit.text, provenance=prov)
    if unit.mono:
        return Block(kind=BlockKind.CODE, text=unit.text, provenance=prov)
    if unit.text.startswith(BULLETS):
        return Block(
            kind=BlockKind.LIST_ITEM, text=unit.text.lstrip("".join(BULLETS) + " "), provenance=prov
        )
    return Block(kind=BlockKind.PARAGRAPH, text=unit.text, provenance=prov)
