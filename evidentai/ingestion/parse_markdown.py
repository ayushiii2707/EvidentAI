"""Markdown parser. Headings come from the Markdown syntax, so structure is exact."""

from markdown_it import MarkdownIt
from markdown_it.token import Token

from evidentai.ingestion.models import (
    Block,
    BlockKind,
    MediaType,
    ParsedDocument,
    Provenance,
    sha256_hex,
)
from evidentai.ingestion.structure import SectionBuilder

PARSER_NAME = "markdown-it"
PARSER_VERSION = "1"


def markdown_engine() -> MarkdownIt:
    return MarkdownIt("commonmark").enable("table")


def inline_text(token: Token) -> str:
    """Plain text of an inline token: markup characters (backticks, emphasis) are dropped."""
    parts: list[str] = []
    for child in token.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type == "softbreak":
            parts.append(" ")
        elif child.type == "hardbreak":
            parts.append("\n")
    return "".join(parts).strip()


def parse_markdown(filename: str, data: bytes) -> ParsedDocument:
    source_sha = sha256_hex(data)
    tokens = markdown_engine().parse(data.decode("utf-8"))
    builder = SectionBuilder(source_sha)
    title: str | None = None
    list_depth = 0
    table_rows: list[list[str]] | None = None
    table_line: int | None = None

    for i, tok in enumerate(tokens):
        line = tok.map[0] + 1 if tok.map else None
        prov = Provenance(line=line)
        match tok.type:
            case "heading_open":
                text = inline_text(tokens[i + 1])
                level = int(tok.tag[1])
                if level == 1 and title is None:
                    title = text
                builder.heading(level, text, prov)
            case "bullet_list_open" | "ordered_list_open":
                list_depth += 1
            case "bullet_list_close" | "ordered_list_close":
                list_depth -= 1
            case "paragraph_open" if table_rows is None:
                kind = BlockKind.LIST_ITEM if list_depth else BlockKind.PARAGRAPH
                builder.block(Block(kind=kind, text=inline_text(tokens[i + 1]), provenance=prov))
            case "fence" | "code_block":
                builder.block(
                    Block(kind=BlockKind.CODE, text=tok.content.rstrip("\n"), provenance=prov)
                )
            case "table_open":
                table_rows, table_line = [], line
            case "tr_open" if table_rows is not None:
                table_rows.append([])
            case "inline" if table_rows is not None and table_rows:
                table_rows[-1].append(inline_text(tok))
            case "table_close" if table_rows is not None:
                text = "\n".join(" | ".join(row) for row in table_rows)
                builder.block(
                    Block(kind=BlockKind.TABLE, text=text, provenance=Provenance(line=table_line))
                )
                table_rows = None

    return ParsedDocument(
        source_sha256=source_sha,
        filename=filename,
        media_type=MediaType.MARKDOWN,
        parser=PARSER_NAME,
        parser_version=PARSER_VERSION,
        page_count=None,
        title=title,
        sections=builder.build(),
    )
