"""Command-line entry point: `evidentai parse <file> [--tree | --chunks | --json]`."""

import argparse
import sys
from pathlib import Path

from evidentai.ingestion import chunk_document, parse_document
from evidentai.ingestion.errors import IngestionError
from evidentai.ingestion.models import ParsedDocument


def _location(page: int | None, line: int | None) -> str:
    return f"p{page}" if page is not None else f"L{line}"


def print_tree(doc: ParsedDocument) -> None:
    print(
        f"{doc.filename}  parser={doc.parser}  pages={doc.page_count}  "
        f"sections={len(doc.sections)}  title={doc.title!r}"
    )
    for w in doc.warnings:
        print(f"  warning: {w}")
    for s in doc.sections:
        label = s.heading or "(preamble)"
        loc = _location(s.heading_provenance.page, s.heading_provenance.line)
        kinds = ",".join(sorted({b.kind.value for b in s.blocks})) or "-"
        print(f"{'  ' * max(s.level - 1, 0)}{label}  [{loc}, blocks={len(s.blocks)}: {kinds}]")


def print_chunks(doc: ParsedDocument, max_words: int) -> None:
    chunks = chunk_document(doc, max_words=max_words)
    print(f"{len(chunks)} chunks (max_words={max_words})")
    for c in chunks:
        loc = (
            f"p{c.page_start}-{c.page_end}"
            if c.page_start is not None
            else f"L{c.line_start}-{c.line_end}"
        )
        preview = c.text.replace("\n", " ")[:70]
        print(f"{c.key[:10]}  {loc:<8} {c.word_count:>4}w  {' > '.join(c.heading_path)}")
        print(f"            {preview}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evidentai")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("parse", help="Validate and parse a PDF or Markdown document")
    p.add_argument("file", type=Path)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--tree", action="store_true", help="Print the section tree (default)")
    mode.add_argument("--chunks", action="store_true", help="Print chunks")
    mode.add_argument("--json", action="store_true", help="Print the parsed document as JSON")
    p.add_argument("--max-words", type=int, default=300)
    args = parser.parse_args(argv)

    try:
        doc = parse_document(args.file.name, args.file.read_bytes())
    except IngestionError as exc:
        print(f"error[{exc.code}]: {exc.message}", file=sys.stderr)
        return 2

    if args.json:
        print(doc.model_dump_json(indent=2))
    elif args.chunks:
        print_chunks(doc, args.max_words)
    else:
        print_tree(doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
