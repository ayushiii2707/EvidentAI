"""Builds the section tree from a flat stream of headings and blocks.

Both parsers emit the same two events in document order, so heading-path logic,
section keys and the preamble rule live in one place.
"""

from collections import Counter

from evidentai.ingestion.models import Block, Provenance, Section, stable_key


class SectionBuilder:
    def __init__(self, source_sha256: str):
        self._source = source_sha256
        self._sections: list[Section] = []
        self._stack: list[Section] = []  # open ancestors of the current position
        self._path_counts: Counter[tuple[str, ...]] = Counter()
        self._current: Section | None = None

    def heading(self, level: int, text: str, provenance: Provenance) -> None:
        if level < 1:
            raise ValueError(f"heading level must be >= 1, got {level}")
        while self._stack and self._stack[-1].level >= level:
            self._stack.pop()
        path = [s.heading for s in self._stack] + [text]
        section = self._new_section(level, text, path, provenance)
        self._stack.append(section)
        self._current = section

    def block(self, block: Block) -> None:
        if not block.text.strip():
            return
        if self._current is None:
            # Text before the first heading (e.g. a notice) lives in a level-0 preamble.
            self._current = self._new_section(0, "", [], block.provenance)
        self._current.blocks.append(block)

    def build(self) -> list[Section]:
        return self._sections

    def _new_section(
        self, level: int, heading: str, path: list[str], provenance: Provenance
    ) -> Section:
        occurrence = self._path_counts[tuple(path)]
        self._path_counts[tuple(path)] += 1
        section = Section(
            key=stable_key(self._source, "section", *path, occurrence),
            parent_key=self._stack[-1].key if self._stack else None,
            level=level,
            heading=heading,
            heading_path=path,
            ordinal=len(self._sections),
            heading_provenance=provenance,
        )
        self._sections.append(section)
        return section
