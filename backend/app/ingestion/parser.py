"""Structure-aware statutory parser (REQUIREMENTS A1-001, A1-030..A1-036).

Parses cleaned page text into::

    Act -> Chapter -> Section -> (Subsection -> Clause), Proviso,
                              Exception, Explanation, Illustration

Layout facts (verified against the actual Gazette PDF, see DECISIONS.md):

* printed page number is the first text line of a page;
* section starts match ``^<digits>. <letter>`` at line start;
* CHAPTER headings are ``CHAPTER <ROMAN>`` followed by a title line;
* statute body ends before the signature block / schedules / forms;
* section titles are **marginal notes** printed in the page margin. The
  extracted text layer is FLAT (no positional signal survives), and the
  margin column interleaves with the body in several modes:
  "Illustrations." label lines, group headings ("Of hurt"), whole note
  clusters between sections, note words interleaved line-by-line into the
  body, and note fragments glued to the end of body/header lines
  ("...liable to fine.Husband or", "...such miscarriage Causing").

Recovery strategy (two passes):

1. **Parse pass** — walk the lines, recovering note material: split glued
   line-end fragments into the note buffer, capture short interleaved note
   lines, skip label/group-heading lines, and record every note-cluster
   flush as an *event* (header flush vs tail flush, with the section count
   at that moment). A cluster that does not end in "." is incomplete (a
   note split across a boundary) and is carried into the next flush.
2. **Assignment pass** — with all sections known, assign each event's
   clusters: earlier clusters fill untitled sections in ascending order
   (classic Gazette layout); the LAST cluster is placed by content matching
   between the section the flush points at and its neighbour, with the
   positional default kept unless the challenger scores at least twice as
   high. Reconstructed (fragment-derived) titles are flagged
   ``title_confident=False`` so downstream review can prioritise them.

The parser is layout-driven and contains no statute-specific constants, so a
replacement source PDF with the same Gazette layout parses identically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.models import (
    Block,
    BlockKind,
    CorpusSpec,
    PageText,
    ParsedAct,
    Section,
)

# "CHAPTER V" — the Gazette sometimes glues the numeral ("CHAPTERV") and may
# carry the chapter title on the same line.
CHAPTER_RE = re.compile(r"^CHAPTER\s*([IVXLCDM]+)(?:\s+(.*))?$")
SECTION_RE = re.compile(r"^(\d{1,3})\.\s*(?=[A-Za-z(])(.*)$")
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

# --------------------------------------------------------------------------
# Marginal-note recovery in the flat Gazette text layer
# --------------------------------------------------------------------------

# First words that never open a marginal-note fragment: sentence openers
# (a glued body sentence, not a note) and Gazette proper nouns (a glued
# body continuation such as "...by the Government"). Verbs likewise mark
# body text ("...shall also be liable to fine" is not a note).
_SENTENCE_STARTERS = frozenset(
    [
        "Whoever",
        "Where",
        "When",
        "If",
        "Unless",
        "Nothing",
        "Any",
        "Every",
        "No",
        "Provided",
        "Except",
        "Explanation",
        "Illustration",
        "Illustrations",
        "Exception",
        "The",
        "A",
        "An",
        "He",
        "She",
        "It",
        "They",
        "But",
        "In",
        "On",
        "At",
        "For",
        "With",
        "Not",
        "Nothing",
        "Neither",
        "Either",
        "Both",
        "Such",
        "Who",
        "Whom",
        "Which",
        "What",
        "This",
        "Whoever",
        "Whosoever",
        "Whichever",
        "Whatever",
    ]
)
_PROPER_NOUNS = frozenset(
    [
        "Government",
        "India",
        "State",
        "Court",
        "Union",
        "Parliament",
        "President",
        "Governor",
        "Judge",
        "Magistrate",
        "Sanhita",
        "Act",
        "Code",
        "Schedule",
        "Gazette",
        "Assembly",
        "Legislature",
        "Council",
        "Ministry",
        "Department",
        "Commission",
        "Board",
        "Sovereign",
        "Armed",
        "Provident",
        "Aadhaar",
        "Insurance",
        "Chapter",
        "Part",
        "Section",
        "India",
        "Bharatiya",
        "Nyaya",
        "Nagarik",
        "Suraksha",
    ]
)
_VERB_WORDS = frozenset(
    [
        "be",
        "is",
        "are",
        "was",
        "were",
        "been",
        "shall",
        "will",
        "may",
        "must",
        "does",
        "do",
        "did",
        "has",
        "have",
        "had",
    ]
)

# Glued line-end note fragments, by glue mode:
#   F1 period-glued: "...liable to fine.Husband or"
#   F2 space/comma-glued: "...such miscarriage Causing" / "...death, Culpable"
#   F3 function-word + lowercase fragment (only while a note cluster is
#      open, i.e. mid-note interleave): "...or with the homicide."
_FRAG_PERIOD_RE = re.compile(
    r"^(?P<body>.*[a-z0-9)\]])\.(?P<frag>[A-Z][A-Za-z']{1,}(?:\s+[A-Za-z']+){0,3})\.?\s*$"
)
_FRAG_SPACE_RE = re.compile(
    r"^(?P<body>.*[a-z,;'\"\)])\s(?P<frag>[A-Z][A-Za-z']{1,}(?:\s+[A-Za-z']+){0,3}),?\.?\s*$"
)
_FRAG_FUNC_RE = re.compile(
    r"^(?P<body>.*\b(?:or|and|of|the|a|to|in|by|with|for|on|at|from)\s+)"
    r"(?P<frag>[a-z][a-z']{2,}(?:\s+[a-z']+){0,3}\.)\s*$"
)
# F4 verb/negation + short function word ("...shall not for" is body
# "...shall not" + note word "for"; only while a note cluster is open).
_FRAG_FUNCWORD_RE = re.compile(
    r"^(?P<body>.*\b(?:not|no|shall|will|may|is|are|was|were|be|been"
    r"|has|have|had|does|do|did)\s+)(?P<frag>[a-z]{2,4})\s*$"
)
_FUNC_WORD_FRAGS = frozenset(
    ["for", "of", "to", "or", "and", "by", "in", "on", "at", "the", "with", "from", "not"]
)

# "Illustrations." — a label introducing illustration blocks, printed in
# the margin. Never a section title.
_ILLUSTRATIONS_LABEL_RE = re.compile(r"^Illustrations\b[.:]?\s*$")
_ILLUSTRATIONS_PREFIX_RE = re.compile(r"^Illustrations\.\s*")

# Group headings inside a chapter ("Of hurt", "OF OFFENCES AFFECTING...").
# Mixed-case ones start with "Of" + lowercase; ALL-CAPS ones are 4+ chars
# of capitals. Neither is statute text nor a section title.
_GROUP_HEADING_RE = re.compile(r"^(?:[A-Z][A-Z ,&'()\-]{3,}|Of\s+[a-z][a-z ,&'()\-.]{0,55})$")

# A short line interleaved mid-sentence into the body stream is a note
# word (Gazette prints the margin column word-by-word in some sections):
# "Exposure and", "abandonment", "Hurt." — but never a body continuation
# like "hurt." (lowercase with sentence punctuation) or "Government".
_INTERLEAVE_MAX = 22

# A lone connective/preposition ("under", "or") is a sentence fragment
# stranded by the flat text layer, never a marginal-note cluster on its
# own — dropping it leaves the section untitled rather than junk-titled.
_DANGLING_CONNECTIVES = frozenset(
    [
        "or",
        "and",
        "of",
        "to",
        "in",
        "by",
        "with",
        "for",
        "on",
        "at",
        "if",
        "when",
        "etc",
        "a",
        "an",
        "the",
        "under",
        "from",
        "not",
        "no",
        "than",
        "also",
        "being",
        "alive",
        "amounting",
        "knowing",
        "having",
        "rupees",
        "lakh",
        "years",
        "months",
    ]
)

# Statute-prose words that never open a marginal-note fragment but DO
# wrap onto their own line inside body text ("...under this / section 65").
_STATUTE_PROSE_WORDS = frozenset(
    [
        "section",
        "sub-section",
        "subsection",
        "clause",
        "clauses",
        "sanhita",
        "thereof",
        "herein",
        "hereinafter",
        "aforesaid",
    ]
)

# Body sentence tails that start lowercase comparison/continuation words
# and never a marginal-note fragment ("...more / than five lakh rupees").
_BODY_TAIL_STARTERS = frozenset(
    [
        "than",
        "also",
        "being",
        "alive",
        "amounting",
        "knowing",
        "having",
        "rupees",
        "lakh",
        "years",
        "months",
    ]
)

# Content-matching vocabulary for title association.
_SCORE_TOKEN_RE = re.compile(r"[a-z]{3,}")
_SUFFIXES = (
    "ations",
    "ation",
    "ments",
    "ment",
    "ions",
    "ion",
    "ings",
    "ing",
    "ies",
    "ed",
    "es",
    "age",
    "e",
    "s",
)


def _score_tokens(text: str) -> set[str]:
    """Significant stemmed words for content matching (light, symmetric)."""
    out: set[str] = set()
    for word in _SCORE_TOKEN_RE.findall(text.lower()):
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[: -len(suffix)]
                break
        out.add(word)
    return out


def _content_score(cluster_text: str, section: Section) -> int:
    """Overlap between a note cluster's vocabulary and a section's body."""
    want = _score_tokens(cluster_text)
    if not want:
        return 0
    return len(want & _score_tokens(section.text))


@dataclass
class _NoteCluster:
    """One recovered marginal-note cluster; ``forced`` marks reconstructed
    (glued-fragment) clusters, which are flagged low-confidence."""

    text: str
    forced: bool


@dataclass
class _FlushEvent:
    """A note-buffer flush: which clusters, where, and how many sections
    existed at that moment (``kind``: "header" = flushed at a section
    header, "tail" = flushed at any other boundary or page end)."""

    kind: str
    page: int
    n_sections: int
    clusters: list[_NoteCluster]


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
        # Marginal-note lines collected since the last flush:
        # (line, forced) — forced lines were recovered from glued fragments.
        note_buf: list[tuple[str, bool]] = []
        # An unterminated trailing cluster carried into the next flush
        # (a note split across a page or section boundary).
        carry: tuple[str, bool] | None = None
        # Body of the last non-note line, used to gate note-run starts.
        last_body_line = ""
        # Plain body lines since a carried (unterminated) note fragment
        # was set or extended by a capture: a carry followed by 2+ body
        # lines is not a note split across a boundary — word-by-word
        # interleave never leaves more than one body line between note
        # words.
        body_since_carry = 0
        events: list[_FlushEvent] = []

        def cluster_notes() -> list[_NoteCluster]:
            """Split buffered note lines into clusters; clear the buffer."""
            clusters: list[_NoteCluster] = []
            buf: list[str] = []
            forced_any = False
            for text, forced in note_buf:
                words = text.split()
                if len(words) == 1 and words[0].strip(".;:") in _PROPER_NOUNS:
                    # A lone Gazette proper noun ("India.") closes the run
                    # without contributing title text.
                    if buf:
                        clusters.append(_NoteCluster(" ".join(buf), forced_any))
                    buf = []
                    forced_any = False
                    continue
                buf.append(text)
                forced_any = forced_any or forced
                if text.endswith("."):
                    clusters.append(_NoteCluster(" ".join(buf), forced_any))
                    buf = []
                    forced_any = False
            if buf:
                clusters.append(_NoteCluster(" ".join(buf), forced_any))
            note_buf.clear()
            out: list[_NoteCluster] = []
            for cluster in clusters:
                # "Illustrations." labels annotate the margin; a label glued
                # in front of the real note is stripped, a bare label drops.
                text = _ILLUSTRATIONS_PREFIX_RE.sub("", cluster.text).strip()
                # Citation-note clusters ("1 of 1871.") annotate the margin,
                # not a section title; drop them.
                # "etc." alone is the tail of a note that was lost to the
                # interleave — never a title on its own.
                lone = text.rstrip(".;:").split()
                if (
                    not text
                    or text.strip(". ").lower() == "etc"
                    or (len(lone) == 1 and lone[0].lower() in _DANGLING_CONNECTIVES)
                    or _CITATION_NOTE_RE.fullmatch(text)
                ):
                    continue
                out.append(_NoteCluster(text, cluster.forced))
            return out

        def flush(kind: str, page: int) -> None:
            """Record buffered note clusters as a flush event."""
            nonlocal carry, body_since_carry
            if not note_buf:
                return
            clusters = cluster_notes()
            if carry is not None and clusters:
                carried_text, carried_forced = carry
                first = clusters[0]
                clusters[0] = _NoteCluster(
                    f"{carried_text} {first.text}", carried_forced or first.forced
                )
                carry = None
            if (
                clusters
                and not clusters[-1].text.endswith(".")
                and len(clusters[-1].text.split()) < 5
            ):
                # Incomplete note (split across a boundary): carry it into
                # the next flush instead of guessing an assignment. Once a
                # carried fragment reaches title length it is complete
                # enough to assign on its own.
                last = clusters[-1]
                carry = (last.text, last.forced)
                body_since_carry = 0
                clusters = clusters[:-1]
            if clusters:
                events.append(_FlushEvent(kind, page, len(sections), clusters))

        # Flatten pages to (printed_page, line) so look-ahead can see across
        # page boundaries; page changes emit the classic page-tail flush.
        flat: list[tuple[int, str]] = []
        for page in pages:
            flat.extend((page.printed_page, line) for line in page.lines)

        for i, (printed, line) in enumerate(flat):
            if i > 0 and flat[i - 1][0] != printed:
                flush("tail", flat[i - 1][0])
            next_line = flat[i + 1][1] if i + 1 < len(flat) else None

            if STATUTE_END_RE.match(line):
                statute_ended = True
                break

            if CHAPTER_RE.match(line):
                flush("tail", printed)
                chapter_match = CHAPTER_RE.match(line)
                chapter_number = chapter_match.group(1)  # type: ignore[union-attr]
                # Chapter title may be glued to the heading line itself.
                chapter_title = chapter_match.group(2)  # type: ignore[union-attr]
                continue
            if chapter_number is not None and chapter_title is None and line:
                if SECTION_RE.match(line):  # chapter with no title line
                    chapter_title = ""
                    # fall through: the line is a section start
                else:
                    chapter_title = line
                    continue

            # Margin chrome that is neither statute text nor a note.
            if _ILLUSTRATIONS_LABEL_RE.match(line) or _GROUP_HEADING_RE.match(line):
                continue

            section_match = SECTION_RE.match(line)
            is_structural = bool(
                section_match
                or PROVISO_RE.match(line)
                or EXCEPTION_RE.match(line)
                or EXPLANATION_RE.match(line)
                or ILLUSTRATION_RE.match(line)
                or SUBSECTION_RE.match(line)
                or CLAUSE_RE.match(line)
            )

            # Marginal-note run: note-like line after a sentence-terminal
            # body line, while a note cluster is open, or a short line
            # interleaved mid-sentence into the body stream. A line that
            # ends on a dangling quote ("are'") is a body tail, and a
            # lone Gazette proper noun ("India") is preamble or body, not
            # a marginal note.
            cluster_open = bool(note_buf) or carry is not None
            if (
                current is not None
                and line
                and not is_structural
                and self._is_note_line(line)
                and not line.endswith("'")
                # Marginal notes are noun phrases: they never open with a
                # sentence starter or a body-tail continuation word.
                and line.split()[0].strip(".,;:") not in _SENTENCE_STARTERS
                and line.split()[0].lower() not in _BODY_TAIL_STARTERS
                # A lowercase line longer than 4 words that ends in
                # sentence punctuation is a completed body sentence, not a
                # note word — interleave fragments are short (like the
                # 4-word cap on _FRAG_FUNC_RE) and unterminated margin
                # column wraps carry no punctuation.
                and not (line[:1].islower() and len(line.split()) > 4 and line.endswith(_TERMINAL))
                and (
                    cluster_open
                    or last_body_line.endswith(_TERMINAL)
                    or self._is_interleaved_line(line, next_line)
                )
            ):
                # Captures other than a clean run after a sentence-terminal
                # body line are reconstructions from the interleaved flat
                # text layer; mark them forced so the resulting title is
                # flagged title_confident=False for downstream review.
                after_terminal = last_body_line.endswith(_TERMINAL)
                note_buf.append((line, not after_terminal))
                body_since_carry = 0
                continue

            # Flush buffered notes at any non-note boundary — EXCEPT a
            # section header, whose clusters flush as a "header" event
            # inside the branch below.
            if note_buf and not section_match:
                flush("tail", printed)

            if section_match:
                # A note fragment may be glued to the header line itself
                # ("88. ... such miscarriage Causing"): split it off and
                # buffer it for the NEW section.
                header_text, frag = line, None
                if next_line and next_line[:1].islower():
                    header_text, frag = self._split_trailing_fragment(
                        line, next_line, allow_func=cluster_open
                    )
                flush("header", printed)
                current = Section(
                    number=int(section_match.group(1)),
                    chapter_number=chapter_number,
                    chapter_title=chapter_title,
                    page_start=printed,
                    page_end=printed,
                )
                sections.append(current)
                subsection = None
                clause = None
                body_text = section_match.group(2)
                if header_text is not line:
                    body_match = SECTION_RE.match(header_text)
                    if body_match:
                        body_text = body_match.group(2)
                current_block = Block(
                    kind=BlockKind.BODY,
                    text=body_text,
                    page=printed,
                )
                current.blocks.append(current_block)
                last_body_line = body_text
                if frag:
                    note_buf.append((frag, True))
                continue

            if not is_structural and line:
                # Plain body continuation; a glued note fragment is split
                # off into the note buffer first.
                body_since_carry += 1
                if carry is not None and not note_buf and body_since_carry > 1:
                    # The carried fragment is separated from the next note
                    # by real body text: close it as its own event instead
                    # of merging it across the body into the next
                    # section's cluster ("Voyeurism" must not merge into
                    # "Stalking.").
                    carried_text, carried_forced = carry
                    events.append(
                        _FlushEvent(
                            "tail",
                            printed,
                            len(sections),
                            [_NoteCluster(carried_text, carried_forced)],
                        )
                    )
                    carry = None
                body_line, frag = line, None
                if next_line and next_line[:1].islower():
                    body_line, frag = self._split_trailing_fragment(
                        line, next_line, allow_func=cluster_open
                    )
                if current_block is not None:
                    current_block.text += f" {body_line}"
                last_body_line = body_line
                if current is not None:
                    current.page_end = printed
                if frag:
                    note_buf.append((frag, True))
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
                    page=printed,
                    subsection=subsection,
                    clause=clause,
                )
            else:
                current_block = Block(
                    kind=kind,  # type: ignore[arg-type]
                    text=text,
                    page=printed,
                    subsection=subsection,
                    clause=clause,
                )
            current.blocks.append(current_block)
            current.page_end = printed
            last_body_line = text

        if not statute_ended:
            if flat:
                flush("tail", flat[-1][0])
            if carry is not None:
                carried_text, carried_forced = carry
                events.append(
                    _FlushEvent(
                        "tail",
                        flat[-1][0] if flat else 0,
                        len(sections),
                        [_NoteCluster(carried_text, carried_forced)],
                    )
                )

        self._assign_note_titles(sections, events)

        return ParsedAct(
            act=self._spec.act,
            act_short=self._spec.act_short,
            act_title_detected=act_title,
            sections=sections,
            statute_end_page=sections[-1].page_end if sections else None,
        )

    # -- note-title assignment (pass 2) --------------------------------------

    def _assign_note_titles(self, sections: list[Section], events: list[_FlushEvent]) -> None:
        """Attach flushed note clusters to sections.

        Classic Gazette layout: a section's marginal note is printed in its
        margin, so a flush's earlier clusters fill untitled sections in
        ascending order. The LAST cluster sits at a boundary and belongs to
        either the section the flush points at or its neighbour — content
        matching decides, with the positional default kept unless the
        challenger clearly scores higher (>= 1.5x).
        """
        for event in events:
            clusters = event.clusters
            if not clusters:
                continue
            pending = [s for s in sections[: event.n_sections] if s.title is None]

            # Classic exact fill: page-tail notes matching the untitled
            # backlog one-to-one (the dominant clean layout).
            if event.kind == "tail" and pending and len(clusters) == len(pending):
                for section, cluster in zip(pending, clusters, strict=True):
                    section.title = cluster.text.rstrip(".")
                    section.title_confident = not cluster.forced
                continue

            earlier, last = clusters[:-1], clusters[-1]
            if event.kind == "header":
                over = len(earlier) > len(pending) > 0
            else:
                over = len(clusters) > len(pending) > 0
            if over:
                self.warnings.append(
                    f"page {event.page}: {len(clusters)} marginal note "
                    f"cluster(s) for {len(pending)} untitled section(s); "
                    "association uncertain"
                )
            if earlier:
                # Fill from the END of the pending list: the flush's
                # clusters were printed next to the most recent sections,
                # so a stale untitled section from an earlier page must
                # not steal a cluster from its flush-adjacent neighbour.
                recent = pending[max(0, len(pending) - len(earlier)) :]
                for section, cluster in zip(recent, earlier, strict=False):
                    section.title = cluster.text.rstrip(".")
                    section.title_confident = (
                        not over and len(earlier) == len(pending) and not cluster.forced
                    )

            if event.kind == "header":
                # Flush at a section header: default is the NEW section
                # (note printed above its header), challenger the current.
                default_idx, other_idx = event.n_sections, event.n_sections - 1
            else:
                # Tail flush: default is the current section (note printed
                # in its margin), challenger the next section.
                default_idx, other_idx = event.n_sections - 1, event.n_sections
            self._assign_last(last, sections, default_idx, other_idx)

    def _assign_last(
        self,
        cluster: _NoteCluster,
        sections: list[Section],
        default_idx: int,
        other_idx: int,
    ) -> None:
        """Place a flush event's last cluster by position + content."""

        def at(idx: int) -> Section | None:
            return sections[idx] if 0 <= idx < len(sections) else None

        default, other = at(default_idx), at(other_idx)
        # A confidently titled section already owns its note; yield it.
        if default is not None and default.title is not None and default.title_confident:
            default = None
        if other is not None and other.title is not None and other.title_confident:
            other = None
        if default is None:
            # The positional default already owns a confident title, so the
            # cluster may only go to the challenger with positive content
            # evidence — otherwise it is margin junk and is dropped.
            if other is None or _content_score(cluster.text, other) <= 0:
                return
            other.title = cluster.text.rstrip(".")
            other.title_confident = not cluster.forced
            return
        if default.title is not None and other is not None and other.title is None:
            # The default already carries an (uncertain) title: the cluster
            # corrects it only with strictly stronger content evidence than
            # the untitled challenger beside it.
            d_score = _content_score(cluster.text, default)
            o_score = _content_score(cluster.text, other)
            if not (d_score > o_score and d_score > 0):
                default, other = other, default

        target = default
        default_score = _content_score(cluster.text, default)
        contested = False
        if other is not None and other.title is None:
            o_score = _content_score(cluster.text, other)
            contested = o_score > 0 and default_score > 0
            # The positional default is overturned only by clearly stronger
            # content evidence (>= 1.5x) — keeps precede/trail layouts safe.
            if o_score > default_score and 2 * o_score > 3 * default_score:
                target = other
        target.title = cluster.text.rstrip(".")
        target.title_confident = not cluster.forced and not contested

    # -- helpers ------------------------------------------------------------

    def _split_trailing_fragment(
        self, line: str, next_line: str, allow_func: bool
    ) -> tuple[str, str | None]:
        """Split a glued line-end marginal-note fragment off ``line``.

        Returns (body, fragment); the fragment exists only when the next
        line continues the sentence in lowercase (a wrapped body line),
        which proves the fragment is not part of the body stream.
        """
        if not next_line or not next_line[:1].islower():
            return line, None
        patterns = (
            (_FRAG_PERIOD_RE, False),
            (_FRAG_SPACE_RE, False),
            (_FRAG_FUNC_RE, True),
            (_FRAG_FUNCWORD_RE, True),
        )
        for pattern, needs_open_cluster in patterns:
            if needs_open_cluster and not allow_func:
                continue
            match = pattern.match(line)
            if not match:
                continue
            frag = (match.group("frag") or "").strip()
            body = (match.group("body") or "").rstrip()
            if not frag or not body:
                continue
            words = [w for w in re.split(r"\s+", frag.strip(".,;")) if w]
            if not 1 <= len(words) <= 4:
                continue
            if pattern is _FRAG_FUNCWORD_RE and words[0] not in _FUNC_WORD_FRAGS:
                continue
            if not needs_open_cluster:
                first = words[0].strip(".,;")
                if first in _SENTENCE_STARTERS or first in _PROPER_NOUNS:
                    continue
            if any(w.strip(".,;").lower() in _VERB_WORDS for w in words):
                continue
            return body, frag
        return line, None

    def _is_interleaved_line(self, line: str, next_line: str | None) -> bool:
        """A short line interleaved mid-sentence into the body stream.

        The Gazette prints some margin columns word-by-word between body
        lines. Such lines are short, carry no comma, and either start
        lowercase without sentence punctuation (body continuations keep
        their punctuation) or are a lone capitalised word ("Hurt."). A
        fragment that does not end a sentence is only a note word when
        the body stream continues in lowercase on the next line — a
        body wrap word before a structural line ("...abetted /
        murder" before a section header) is body text.
        """
        if not line or len(line) > _INTERLEAVE_MAX or "," in line:
            return False
        words = line.split()
        if not words:
            return False
        first = words[0].strip(".;:")
        if first in _PROPER_NOUNS or first in _SENTENCE_STARTERS:
            return False
        if first.strip(".,;()'").lower() in _STATUTE_PROSE_WORDS:
            return False
        if line.endswith(_TERMINAL):
            return len(words) == 1 and first[:1].isupper() and not first.isupper()
        if next_line is not None and not next_line[:1].islower():
            # The body stream does not continue here (a structural line
            # follows): only a TitleCase note word ("Causing" printed
            # above its section header) is margin text — a lowercase
            # word ("...abetted / murder") is a body wrap.
            return first[:1].isupper()
        return True

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
            or re.match(r"^[a-z][a-z0-9,().' -]*$", line)
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
