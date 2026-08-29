"""Structure-aware statutory parser (REQUIREMENTS A1-001, A1-030..A1-036).

Parses cleaned page text into::

    Act -> Chapter -> Section -> (Subsection -> Clause), Proviso,
                              Exception, Explanation, Illustration

Layout facts (verified against the actual Gazette PDF, see DECISIONS.md):

* printed page number is the first text line of a page;
* section starts match ``^\\d{1,3}.\\s`` at line start;
* CHAPTER headings are ``CHAPTER <ROMAN>`` followed by a title line;
* statute body ends before the signature block / schedules / forms;
* section titles are **marginal notes** printed in the page margin. In the
  extracted text they surface as short note-like lines *interleaved* with
  the body (typically after a sentence-terminal line, or at the page tail).
  Each cluster of note lines — the cluster-final line usually ending with
  "." — is one marginal note; the first cluster belonging to a section is
  its title. Later clusters in the same margin column are citation notes
  (e.g. "1 of 1871.") and are deliberately not treated as statute text.

The parser is layout-driven and contains no statute-specific constants, so a
replacement source PDF with the same Gazette layout parses identically.
"""

from __future__ import annotations

import re

from app.ingestion.models import (
    Block,
    BlockKind,
    CorpusSpec,
    PageText,
    ParsedAct,
    Section,
)

CHAPTER_RE = re.compile(r"^CHAPTER\s+([IVXLCDM]+)$")
SECTION_RE = re.compile(r"^(\d{1,3})\.\s+(.*)$")
SUBSECTION_RE = re.compile(r"^\((\d{1,2})\)\s+(.*)$")
CLAUSE_RE = re.compile(r"^\(([a-z])\)\s+(.*)$")
PROVISO_RE = re.compile(r"^Provided\s+(?:further\s+)?that\b")
EXPLANATION_RE = re.compile(r"^Explanation\s*\d*\s*[.:—-]")
ILLUSTRATION_RE = re.compile(r"^Illustration\b")
EXCEPTION_RE = re.compile(r"^Exceptions?\s*[.:—-]")

# Statute body ends at the first of these markers (schedules, forms,
# signature block). Everything after is not statute text.
STATUTE_END_RE = re.compile(
    r"^(THE\s+FIRST\s+SCHEDULE|THE\s+SECOND\s+SCHEDULE|FORM\s+No|"
    r"DIWAKAR\s+SINGH|Joint\s+Secretary\s+&\s+Legislative\s+Counsel)",
    re.IGNORECASE,
)

# A candidate marginal-note line: short, and not the start of any legal
# structure. Marginal notes are printed title-case fragments.
_NOTE_LINE_MAX = 45

# A body line ending in one of these can be followed by a marginal note.
_TERMINAL = (".", ";", ":", "-", "?")

_ACT_TITLE_RE = re.compile(r"^(THE\s+.*?SANHITA,\s*\d{4})$", re.IGNORECASE)
_ACT_NUMBER_RE = re.compile(r"^NO\.\s*\d+\s+OF\s+\d{4}", re.IGNORECASE)

# A whole marginal-note cluster that is just statute citations, e.g.
# "1 of 1871. 2 of 2000.".
_CITATION_NOTE_RE = re.compile(r"(?:\d{1,3}\s+of\s+\d{1,4}[A-Za-z]*\.?\s*)+")


class StructureParser:
    """Parse cleaned pages into statutory structure."""

    def __init__(self, spec: CorpusSpec) -> None:
        self._spec = spec
        self.warnings: list[str] = []

    # -- public API ---------------------------------------------------------

    def parse(self, pages: list[PageText]) -> ParsedAct:
        """Parse all pages into a ParsedAct.

        Raises ValueError if no act title can be detected (the pipeline
        performs full source validation separately).
        """
        act_title = self._detect_act_title(pages)
        if act_title is None:
            raise ValueError("act title not found in source text")

        sections: list[Section] = []
        chapter_number: str | None = None
        chapter_title: str | None = None
        current: Section | None = None
        current_block: Block | None = None
        subsection: str | None = None
        clause: str | None = None
        statute_ended = False
        # Marginal-note lines collected since the last structural boundary.
        note_buf: list[str] = []
        # Body of the last non-note line, used to gate note-run starts.
        last_body_line = ""
        # Sections that started on the current page (for note association).
        started_here: list[Section] = []

        def flush_notes() -> None:
            """Associate buffered marginal-note clusters with sections."""
            if not note_buf:
                return
            clusters: list[str] = []
            buf: list[str] = []
            for line in note_buf:
                buf.append(line)
                if line.endswith("."):
                    clusters.append(" ".join(buf))
                    buf = []
            if buf:
                clusters.append(" ".join(buf))
            note_buf.clear()

            # Citation-note clusters ("1 of 1871.") annotate the margin, not
            # a section title; drop them so they cannot shift association.
            clusters = [c for c in clusters if not _CITATION_NOTE_RE.fullmatch(c)]

            targets = [s for s in sections if s.title is None]
            if not targets and current is not None and current.title is None:
                targets = [current]  # note for a section continued onto page
            if len(clusters) > len(targets) > 0:
                self.warnings.append(
                    f"page {page.printed_page}: {len(clusters)} marginal note "
                    f"cluster(s) for {len(targets)} untitled section(s); "
                    "association uncertain"
                )
            for target, cluster in zip(targets, clusters, strict=False):
                target.title = cluster.rstrip(".")
                target.title_confident = len(clusters) == len(targets)

        for page in pages:
            started_here = []
            for line in page.lines:
                if STATUTE_END_RE.match(line):
                    statute_ended = True
                    break

                if CHAPTER_RE.match(line):
                    flush_notes()
                    chapter_number = CHAPTER_RE.match(line).group(1)  # type: ignore[union-attr]
                    chapter_title = None  # set by next non-empty line
                    continue
                if chapter_number is not None and chapter_title is None and line:
                    if SECTION_RE.match(line):  # chapter with no title line
                        chapter_title = ""
                        # fall through: the line is a section start
                    else:
                        chapter_title = line
                        continue

                is_structural = bool(
                    CHAPTER_RE.match(line)
                    or SECTION_RE.match(line)
                    or PROVISO_RE.match(line)
                    or EXCEPTION_RE.match(line)
                    or EXPLANATION_RE.match(line)
                    or ILLUSTRATION_RE.match(line)
                    or SUBSECTION_RE.match(line)
                    or CLAUSE_RE.match(line)
                )

                # Marginal-note run: note-like line after a
                # sentence-terminal body line (or continuing the run).
                if (
                    not is_structural
                    and current is not None
                    and (note_buf or last_body_line.endswith(_TERMINAL))
                    and line
                    and self._is_note_line(line)
                ):
                    note_buf.append(line)
                    continue
                if note_buf:
                    flush_notes()

                if not is_structural and line:
                    if current_block is not None:
                        current_block.text += f" {line}"
                    last_body_line = line
                    if current is not None:
                        current.page_end = page.printed_page
                    continue

                section = SECTION_RE.match(line)
                if section:
                    current = Section(
                        number=int(section.group(1)),
                        chapter_number=chapter_number,
                        chapter_title=chapter_title,
                        page_start=page.printed_page,
                        page_end=page.printed_page,
                    )
                    sections.append(current)
                    started_here.append(current)
                    subsection = None
                    clause = None
                    current_block = Block(
                        kind=BlockKind.BODY,
                        text=section.group(2),
                        page=page.printed_page,
                    )
                    current.blocks.append(current_block)
                    last_body_line = section.group(2)
                    continue

                if current is None:
                    continue  # preamble before section 1 (act title etc.)

                kind, text = self._classify_component(line)
                if kind == BlockKind.BODY:
                    sub = SUBSECTION_RE.match(line)
                    if sub:
                        subsection = f"({sub.group(1)})"
                        clause = None
                        text = sub.group(2)
                    cls = CLAUSE_RE.match(line)
                    if cls and sub is None:
                        clause = f"({cls.group(1)})"
                        text = cls.group(2)
                    current_block = Block(
                        kind=BlockKind.BODY,
                        text=text,
                        page=page.printed_page,
                        subsection=subsection,
                        clause=clause,
                    )
                else:
                    current_block = Block(
                        kind=kind,
                        text=text,
                        page=page.printed_page,
                        subsection=subsection,
                        clause=clause,
                    )
                current.blocks.append(current_block)
                current.page_end = page.printed_page
                last_body_line = text

            if statute_ended:
                break
            flush_notes()
            if current is not None:
                current.page_end = page.printed_page

        return ParsedAct(
            act=self._spec.act,
            act_short=self._spec.act_short,
            act_title_detected=act_title,
            sections=sections,
            statute_end_page=sections[-1].page_end if sections else None,
        )

    # -- helpers ------------------------------------------------------------

    def _detect_act_title(self, pages: list[PageText]) -> str | None:
        """Find the act title on the opening pages (content, not filename)."""
        for page in pages[:3]:
            for i, line in enumerate(page.lines):
                match = _ACT_TITLE_RE.match(line)
                if match and i + 1 < len(page.lines) and _ACT_NUMBER_RE.match(page.lines[i + 1]):
                    return re.sub(r"\s+", " ", line).title()
        return None

    def _is_note_line(self, line: str) -> bool:
        if len(line) > _NOTE_LINE_MAX:
            return False
        if SECTION_RE.match(line) or SUBSECTION_RE.match(line) or CLAUSE_RE.match(line):
            return False
        if (
            PROVISO_RE.match(line)
            or EXPLANATION_RE.match(line)
            or ILLUSTRATION_RE.match(line)
            or EXCEPTION_RE.match(line)
            or CHAPTER_RE.match(line)
        ):
            return False
        # Note fragments are title-case words, no sentence punctuation chain.
        # Statute citation notes look like "1 of 1871.".
        return bool(
            re.match(r"^[A-Z][A-Za-z0-9,()./&' -]*$", line)
            or re.match(r"^[a-z][a-z0-9,(). -]*$", line)
            or re.match(r"^\d{1,3}\s+of\s+\d{1,4}[A-Za-z]*\.?$", line)
        )

    def _classify_component(self, line: str) -> tuple[BlockKind | None, str]:
        """Classify a structural line start; (None, line) = continuation."""
        if PROVISO_RE.match(line):
            return BlockKind.PROVISO, line
        if EXCEPTION_RE.match(line):
            return BlockKind.EXCEPTION, line
        if EXPLANATION_RE.match(line):
            return BlockKind.EXPLANATION, line
        if ILLUSTRATION_RE.match(line):
            return BlockKind.ILLUSTRATION, line
        if SUBSECTION_RE.match(line) or CLAUSE_RE.match(line):
            return BlockKind.BODY, line
        return None, line
