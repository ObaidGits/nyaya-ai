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
   lines and note runs (a sentence-starter word such as "Every member"
   may open a run when the following line is also note-like), skip
   label/group-heading lines, and record every note-cluster flush as an
   *event* (header flush vs tail flush, with the section count at that
   moment). A cluster that does not end in "." is incomplete (a note
   split across a boundary) and is carried into the next flush — across a
   page boundary at any length, and the carry is extended only by
   lowercase continuation words: a line that opens a new capitalised note
   closes the carried cluster instead of merging into it.
2. **Assignment pass** — with all sections known, assign each event's
   clusters by CONTENT: the flat text layer emits a section's marginal
   note both above its header and below the previous section's body, so
   flush position alone cannot decide ownership. A cluster goes to the
   neighbouring section whose body its tokens actually cover (rarity-
   weighted against document frequency, so statute boilerplate such as
   "imprisonment" cannot fake a match); the positional default decides
   only exact content ties. Earlier clusters fill untitled sections in
   ascending order. Reconstructed (fragment-derived) or contested titles
   are flagged ``title_confident=False`` so downstream review can
   prioritise them — an honest review flag beats a confident guess.

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
    r"(?P<frag>[a-z][a-z']{2,}(?:\s+[a-z']+){0,1}\.)\s*$"
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
        "against",
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
    "ly",
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


def _norm_cluster_text(text: str) -> str:
    """Case/punctuation-folded cluster text for duplicate detection."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _df_weights(sections: list[Section]) -> dict[str, float]:
    """Rarity weight per token across the act (data-driven, no word lists).

    A marginal note claims a section by the words it shares with that
    section's body. Statute boilerplate ("imprisonment", "offence",
    "punishable") appears in a large share of sections and matches
    everywhere, so it must carry almost no evidence. Tokens found in <=5%
    of sections carry full weight, <=15% reduced weight, anything more
    near-zero.
    """
    n = max(1, len(sections))
    df: dict[str, int] = {}
    for section in sections:
        for token in _score_tokens(section.text):
            df[token] = df.get(token, 0) + 1
    weights: dict[str, float] = {}
    for token, count in df.items():
        frac = count / n
        if frac <= 0.05:
            weights[token] = 1.0
        elif frac <= 0.15:
            weights[token] = 0.3
        else:
            weights[token] = 0.05
    return weights


@dataclass(frozen=True)
class _TitleMatch:
    """How well a note cluster describes a section's body.

    ``coverage`` — share of the cluster's tokens found in the section
    (plain recall: a marginal note paraphrases its own section, so its
    tokens should all be present). ``opening`` — the same share against
    the section's OPENING block only; the marginal note paraphrases the
    enacting formula, which starts the section, so this breaks coverage
    ties against neighbours whose illustrations merely reuse the words.
    ``strength`` — coverage weighted by token rarity. ``distinctive`` —
    at least one rare (full-weight) token matched; a match with no
    distinctive token is boilerplate and never justifies confidence.
    """

    coverage: float
    opening: float
    strength: float
    distinctive: bool


def _title_match(cluster_text: str, section: Section, weights: dict[str, float]) -> _TitleMatch:
    want = _score_tokens(cluster_text)
    if not want:
        return _TitleMatch(0.0, 0.0, 0.0, False)
    have = _score_tokens(section.text)
    opening_text = section.blocks[0].text if section.blocks else section.text
    opening_have = _score_tokens(opening_text)
    matched = 0
    opening_matched = 0
    weight_sum = 0.0
    matched_weight = 0.0
    best = 0.0
    for token in want:
        # A token unseen in any section is distinctive by definition.
        w = weights.get(token, 1.0)
        weight_sum += w
        if token in have:
            matched += 1
            matched_weight += w
            best = max(best, w)
        if token in opening_have:
            opening_matched += 1
    if weight_sum <= 0:
        return _TitleMatch(0.0, 0.0, 0.0, False)
    return _TitleMatch(
        coverage=matched / len(want),
        opening=opening_matched / len(want),
        strength=matched_weight / weight_sum,
        distinctive=best >= 1.0,
    )


def _clearly_better(a: _TitleMatch, b: _TitleMatch) -> bool:
    """A is a clearly stronger description of a section than B."""
    if a.coverage >= b.coverage + 0.2:
        return True
    if a.coverage >= b.coverage - 1e-9 and a.opening >= b.opening + 0.2:
        return True
    return (
        a.coverage >= b.coverage - 1e-9
        and a.strength > b.strength
        and a.strength >= 1.5 * b.strength
    )


def _match_key(match: _TitleMatch) -> tuple[float, float, float]:
    """Comparison order between two sections' matches of one cluster."""
    return (match.coverage, match.opening, match.strength)


def _decisive(match: _TitleMatch, rival: _TitleMatch | None) -> bool:
    """The match is strong enough to assert a title without review."""
    if match.coverage < 0.6 or not match.distinctive:
        return False
    if rival is None:
        return True
    return (
        match.coverage >= rival.coverage + 0.1
        or match.opening >= rival.opening + 0.1
        or match.strength >= 1.5 * rival.strength
    )


def _pair_confirmed(cluster_text: str, section: Section, weights: dict[str, float]) -> bool:
    """Content confirmation for a POSITIONAL fill (counts unequal or mid-page).

    Plain coverage is not enough: stopwords ("for", "or") and statute
    boilerplate ("intend", "person") can fake a 0.3 recall against almost
    any section. A positional fill is confirmed only when the cluster
    covers the section AND shares at least one rare (full-weight) token
    with it.
    """
    match = _title_match(cluster_text, section, weights)
    return match.coverage >= 0.3 and match.distinctive


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
    header, "tail" = flushed at any other boundary or page end).

    ``inner`` marks a flush that happened while the current section was
    already in full flow (several blocks deep) — such clusters were
    captured inside that section's text stream and have a much stronger
    positional claim to it than boundary-flush clusters.
    """

    kind: str
    page: int
    n_sections: int
    clusters: list[_NoteCluster]
    inner: bool = False
    # Fired at a page boundary (the classic Gazette page-tail layout):
    # the flush's clusters are the notes printed on that page.
    page_end: bool = False


class StructureParser:
    """Parse cleaned pages into statutory structure."""

    def __init__(self, spec: CorpusSpec) -> None:
        self._spec = spec
        self.warnings: list[str] = []
        # Note-flush events from the most recent parse (diagnostics).
        self.last_events: list[_FlushEvent] = []

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

        def cluster_notes(carry: tuple[str, bool] | None = None) -> list[_NoteCluster]:
            """Split buffered note lines into clusters; clear the buffer.

            A carried (unterminated) note fragment opens the first cluster
            and is extended only by lowercase continuation words: a line
            that opens a new capitalised note ("Liability of", after the
            carried "... committed; if / not committed") closes the carried
            cluster instead of merging two sections' notes into one.
            """
            clusters: list[_NoteCluster] = []
            buf: list[str] = []
            forced_any = False
            if carry is not None:
                buf.append(carry[0])
                forced_any = carry[1]
            for text, forced in note_buf:
                words = text.split()
                if len(words) == 1 and words[0].strip(".;:") in _PROPER_NOUNS:
                    # Checked BEFORE the capital split below: a lone proper
                    # noun is a margin word ("Court" inside "relating to /
                    # Court / proceedings"), not the start of a new note.
                    if text.endswith(_TERMINAL):
                        # A lone Gazette proper noun ("India.") closes the
                        # run without contributing title text.
                        if buf:
                            clusters.append(_NoteCluster(" ".join(buf), forced_any))
                        buf = []
                        forced_any = False
                        continue
                    buf.append(text)
                    continue
                if buf and text[:1].isupper() and not buf[-1].endswith(_TERMINAL) and len(buf) >= 2:
                    # A new capitalised note begins while the current
                    # cluster is still open (previous line carries no
                    # terminal): close the open cluster instead of merging
                    # two sections' notes ("... prevent other harm / Act
                    # of a child under seven"). Short buffers (<2 lines)
                    # are usually note heads whose continuation is itself
                    # capitalised, so they keep merging.
                    clusters.append(_NoteCluster(" ".join(buf), forced_any))
                    buf = []
                    forced_any = False
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
                words = text.split()
                if words and text[:1].islower() and words[0].lower() in _DANGLING_CONNECTIVES:
                    # A cluster opening with a dangling connective is a
                    # continuation fragment; a capitalised word later in it
                    # starts the real note ("of abetment Abetment of a
                    # thing" — the prefix is the tail of a lost note).
                    for j in range(1, len(words)):
                        if words[j][:1].isupper():
                            text = " ".join(words[j:]).strip()
                            words = text.split()
                            break
                # Citation-note clusters ("1 of 1871.") annotate the margin,
                # not a section title; drop them.
                # "etc." alone is the tail of a note that was lost to the
                # interleave — never a title on its own.
                lone = text.rstrip(".;:").split()
                if (
                    not text
                    or text.strip(". ").lower() == "etc"
                    or (len(lone) == 1 and lone[0].lower() in _DANGLING_CONNECTIVES)
                    or (len(lone) == 1 and lone[0] in _PROPER_NOUNS)
                    or _CITATION_NOTE_RE.fullmatch(text)
                ):
                    continue
                out.append(_NoteCluster(text, cluster.forced))
            return out

        def flush(kind: str, page: int, *, page_end: bool = False, inner: bool = False) -> None:
            """Record buffered note clusters as a flush event."""
            nonlocal carry, body_since_carry
            if not note_buf:
                return
            clusters = cluster_notes(carry)
            carry = None
            if (
                clusters
                and not clusters[-1].text.endswith(".")
                and (page_end or len(clusters[-1].text.split()) < 5)
            ):
                # Incomplete note (split across a boundary): carry it into
                # the next flush instead of guessing an assignment. Across
                # a page boundary a note may be split at ANY length (the
                # margin column runs to the last printed line); mid-page,
                # only a short fragment is ambiguous enough to defer. Once
                # a carried fragment reaches title length mid-page it is
                # complete enough to assign on its own.
                last = clusters[-1]
                carry = (last.text, last.forced)
                body_since_carry = 0
                clusters = clusters[:-1]
            if clusters:
                events.append(
                    _FlushEvent(kind, page, len(sections), clusters, inner=inner, page_end=page_end)
                )

        # Flatten pages to (printed_page, line) so look-ahead can see across
        # page boundaries; page changes emit the classic page-tail flush.
        flat: list[tuple[int, str]] = []
        for page in pages:
            flat.extend((page.printed_page, line) for line in page.lines)

        for i, (printed, line) in enumerate(flat):
            if i > 0 and flat[i - 1][0] != printed:
                flush("tail", flat[i - 1][0], page_end=True)
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
                and (
                    self._is_note_line(line)
                    # Internal-";" fragments continue an open cluster only.
                    or (cluster_open and self._is_semicolon_note_continuation(line))
                )
                and not line.endswith("'")
                # Marginal notes are noun phrases: they never open with a
                # sentence starter or a body-tail continuation word.
                and (
                    line.split()[0].strip(".,;:") not in _SENTENCE_STARTERS
                    # A sentence-starter word CAN open a note run: notes
                    # often share their opening words with the body
                    # ("Every member of unlawful assembly guilty..."). The
                    # run shape (another short note-like line follows)
                    # separates it from a body sentence wrap.
                    or self._is_note_run_start(line, next_line)
                )
                and line.split()[0].lower() not in _BODY_TAIL_STARTERS
                # A lowercase line longer than 4 words that ends in
                # sentence punctuation is a completed body sentence, not a
                # note word — interleave fragments are short (like the
                # 4-word cap on _FRAG_FUNC_RE) and unterminated margin
                # column wraps carry no punctuation.
                and not (line[:1].islower() and len(line.split()) > 4 and line.endswith(_TERMINAL))
                # A lowercase line carrying sentence punctuation while the
                # BODY sentence is still unterminated and no note run is
                # open completes the body sentence ("...is said to cause /
                # hurt.") — margin wrap words carry no punctuation, and a
                # fresh run never opens on a body tail. An OPEN run's
                # final word ("...question.") is margin text.
                and not (
                    carry is None
                    and (not note_buf or note_buf[-1][0].endswith(_TERMINAL))
                    and line[:1].islower()
                    and len(line.split()) <= 4
                    and line.endswith(_TERMINAL)
                    and not last_body_line.endswith(_TERMINAL)
                )
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
                flush(
                    "tail",
                    printed,
                    inner=current is not None and len(current.blocks) > 1,
                )

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

        # Kept for diagnostics: the raw note-flush events behind pass 2.
        self.last_events = events
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
        ascending order — but only where the cluster's tokens actually
        cover the section (content confirms the fill). The LAST cluster
        sits at a boundary between two sections and belongs to whichever
        body it describes: the flat text layer emits a section's note both
        above its header and below the previous section's body, so flush
        position alone cannot decide. The positional default decides only
        exact content ties; an assignment without decisive content
        evidence is flagged title_confident=False for review.
        """
        weights = _df_weights(sections)
        prepared: list[tuple[_FlushEvent, list[_NoteCluster]]] = []
        for event in events:
            clusters = [c for c in event.clusters if c.text]
            clusters = self._dedupe_clusters(clusters)
            clusters = self._merge_split_clusters(clusters)
            clusters = self._append_continuations(clusters, sections, event, weights)
            clusters = [c for c in clusters if not self._is_body_echo(c, sections, event)]
            prepared.append((event, clusters))
            if not clusters:
                continue
            pending = [s for s in sections[: event.n_sections] if s.title is None]

            # Classic exact fill: PAGE-TAIL notes matching the untitled
            # backlog one-to-one (the dominant clean Gazette layout — the
            # flush fired at a page boundary and every cluster was printed
            # next to its section on that page). The fill is positional;
            # content only gates CONFIDENCE, because a marginal note is not
            # always a body paraphrase ("Short title" shares no word with
            # the section body it names). A backlog section printed pages
            # earlier is NOT at this page's tail — a cluster must not be
            # gifted to it positionally.
            if (
                event.kind == "tail"
                and event.page_end
                and pending
                and len(clusters) == len(pending)
                and all(s.page_end >= event.page - 1 for s in pending)
            ):
                for section, cluster in zip(pending, clusters, strict=True):
                    match = _title_match(cluster.text, section, weights)
                    section.title = cluster.text.rstrip(".")
                    section.title_confident = (
                        not cluster.forced and match.distinctive and match.coverage >= 0.6
                    )
                continue

            # Mid-page 1:1 fill: cluster count matches the untitled backlog
            # and every pair is content-confirmed (coverage >= 0.3 AND a
            # distinctive token — a stopword-only overlap like "for/intend"
            # must not confirm a fill). The floor rejects count-matching
            # but unrelated clusters (a single boundary cluster facing a
            # stale backlog), which must fall through to content
            # arbitration below.
            if (
                pending
                and len(clusters) == len(pending)
                and all(
                    _pair_confirmed(c.text, s, weights)
                    for s, c in zip(pending, clusters, strict=True)
                )
            ):
                for section, cluster in zip(pending, clusters, strict=True):
                    match = _title_match(cluster.text, section, weights)
                    section.title = cluster.text.rstrip(".")
                    section.title_confident = (
                        not cluster.forced and match.distinctive and match.coverage >= 0.6
                    )
                continue
            # Multi-cluster boundary events: order-preserving content
            # matching against the untitled sections around the boundary.
            if len(clusters) >= 2 and self._window_assign(clusters, sections, event, weights):
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
                # Counts did not match one-to-one, so position alone is
                # NOT trusted: each fill needs content confirmation.
                recent = pending[max(0, len(pending) - len(earlier)) :]
                for section, cluster in zip(recent, earlier, strict=False):
                    match = _title_match(cluster.text, section, weights)
                    if not _pair_confirmed(cluster.text, section, weights):
                        continue
                    section.title = cluster.text.rstrip(".")
                    section.title_confident = (
                        not over
                        and len(earlier) == len(pending)
                        and not cluster.forced
                        and match.distinctive
                        and match.coverage >= 0.6
                    )

            if event.kind == "header":
                # Flush at a section header: default is the NEW section
                # (note printed above its header), challenger the current.
                default_idx, other_idx = event.n_sections, event.n_sections - 1
            else:
                # Tail flush: default is the current section (note printed
                # in its margin), challenger the next section.
                default_idx, other_idx = event.n_sections - 1, event.n_sections
            self._assign_last(last, sections, default_idx, other_idx, weights, event)

        self._merge_cross_event_fragments(prepared, sections, weights)
        self._reject_fragment_titles(sections)
        self._sweep_unassigned_clusters(prepared, sections, weights)

    @staticmethod
    def _is_fragment_text(text: str) -> bool:
        """A cluster/title that is a stranded fragment, not a note.

        Marginal notes are TitleCase noun phrases ending on a content
        word. Text that starts lowercase or ends on a dangling
        connective/continuation word is a mid-phrase fragment of the
        flat text layer — never presentable as a title.
        """
        words = text.split()
        if not words:
            return True
        # Prepositions legitimately end Gazette notes ("...not otherwise
        # provided for"); only clear continuation adverbs mark a fragment.
        bad_endings = _BODY_TAIL_STARTERS | {
            "other",
            "when",
            "acting",
            "against",
            "both",
            "not",
        }
        return text[:1].islower() or words[-1].strip(".,;:").lower() in bad_endings

    @staticmethod
    def _fragment_tail_only(text: str) -> bool:
        """Text that ends well but opens lowercase: a continuation tail
        (or a lost note head), never a standalone title."""
        words = text.split()
        if not words:
            return True
        bad_endings = _BODY_TAIL_STARTERS | {
            "other",
            "when",
            "acting",
            "against",
            "both",
            "not",
        }
        return words[-1].strip(".,;:").lower() in bad_endings

    @staticmethod
    def _reject_fragment_titles(sections: list[Section]) -> None:
        """Drop titles that are body fragments, not marginal notes.

        Keeping a fragment would present a wrong title; the section is
        returned to untitled (flagged for review) instead. Fragments were
        always title_confident=False, so no confident title is withdrawn.
        Trailing ", etc" is a legitimate Gazette title ending and kept.
        """
        for section in sections:
            if section.title and StructureParser._is_fragment_text(section.title):
                section.title = None
                section.title_confident = False

    @staticmethod
    def _merge_cross_event_fragments(
        prepared: list[tuple[_FlushEvent, list[_NoteCluster]]],
        sections: list[Section],
        weights: dict[str, float],
    ) -> None:
        """Rejoin a note split across TWO flush events.

        A note broken at a page/section boundary flushes its head in one
        event and its lowercase tail in the next, so the in-event merge
        cannot see the pair. An unconfirmed title that ends DANGLING (on
        a connective) is extended by a nearby unused lowercase-start
        cluster when the merged text describes the section at least as
        well as the head alone (same contract as _append_continuations).
        """
        used = {_norm_cluster_text(s.title) for s in sections if s.title}
        tails: list[tuple[int, _NoteCluster]] = []
        for event, clusters in prepared:
            for cluster in clusters:
                norm = _norm_cluster_text(cluster.text.rstrip("."))
                if (
                    cluster.text[:1].islower()
                    and norm not in used
                    and not StructureParser._fragment_tail_only(cluster.text)
                ):
                    tails.append((event.n_sections, cluster))
        for idx, section in enumerate(sections):
            title = section.title
            if not title or section.title_confident or not StructureParser._is_fragment_text(title):
                continue
            head = title.rsplit(maxsplit=1)[-1].lower()
            if head not in _DANGLING_CONNECTIVES:
                continue
            before = _title_match(title, section, weights).coverage
            for pos, (n_sec, tail) in enumerate(tails):
                if abs(n_sec - (idx + 1)) > 1:
                    continue
                merged = f"{title} {tail.text.rstrip('.')}"
                after = _title_match(merged, section, weights).coverage
                # A dangling head scores near-perfectly on its own words;
                # the tail's extra tokens can only dilute coverage. The
                # dangling ending itself proves incompleteness, so the
                # bar is that the merged note still describes the section.
                if after >= 0.5 and after >= before - 0.3:
                    section.title = merged
                    tails.pop(pos)
                    break

    def _sweep_unassigned_clusters(
        self,
        prepared: list[tuple[_FlushEvent, list[_NoteCluster]]],
        sections: list[Section],
        weights: dict[str, float],
    ) -> None:
        """Final pass: give still-untitled sections their leftover notes.

        The per-event passes are conservative: a cluster whose content
        match is weak (not distinctive) is skipped even when it is the
        ONLY plausible candidate left, and a single-cluster event can
        leave the true owner one section off the arbitration pair. After
        all events, every section holds at most one title, so the set of
        cluster texts that no section owns is known exactly. Each
        still-untitled section takes the best-matching unused cluster
        from a nearby flush (within 3 sections), provided the match beats
        every other untitled section's claim on that cluster by a clear
        margin. Sweep-assigned titles are always title_confident=False —
        an honest review flag, never a confident guess.
        """
        used = {_norm_cluster_text(s.title) for s in sections if s.title}
        candidates: list[tuple[int, _NoteCluster]] = []
        long_fragments: list[tuple[int, _NoteCluster]] = []
        for event, clusters in prepared:
            for cluster in clusters:
                text = cluster.text.rstrip(".")
                if _norm_cluster_text(text) in used:
                    continue
                if not self._is_fragment_text(text):
                    candidates.append((event.n_sections, cluster))
                elif len(text.split()) >= 4 and text[-1].isalpha() and text[-1] not in ".;":
                    # A LONG lowercase fragment can still be a near-complete
                    # note ("good faith for benefit of a person without
                    # consent" = s.30's title minus its first words): body
                    # fragments are short/generic, these carry the note's
                    # distinctive content. Kept in a separate, stricter pool.
                    long_fragments.append((event.n_sections, cluster))
        # Reattach split heads: an unused cluster ending on a dangling word
        # ("Culpable homicide by causing death of person other") is half a
        # note; an adjacent unused lowercase cluster is its tail ("whose
        # death was intended"). The merged text is the real marginal note.
        merged_texts: list[tuple[int, str]] = []
        consumed: set[int] = set()
        flat: list[tuple[int, _NoteCluster, int]] = [
            (event.n_sections, cluster, ei)
            for ei, (event, clusters) in enumerate(prepared)
            for cluster in clusters
        ]
        for hi, (n_head, head, hei) in enumerate(flat):
            head_text = head.text.rstrip(".")
            if (
                self._is_fragment_text(head_text)
                and head_text.rsplit(maxsplit=1)[-1].lower() not in _DANGLING_CONNECTIVES
            ):
                continue
            if _norm_cluster_text(head_text) in used or hi in consumed:
                continue
            for ti, (n_tail, tail, tei) in enumerate(flat):
                if ti == hi or ti in consumed or tei < hei:
                    continue
                tail_text = tail.text.rstrip(".")
                if not tail_text[:1].islower() or self._fragment_tail_only(tail_text):
                    continue
                if _norm_cluster_text(tail_text) in used:
                    continue
                if abs(n_tail - n_head) > 1:
                    continue
                merged = f"{head_text} {tail_text}"
                if not self._is_fragment_text(merged):
                    merged_texts.append((n_head, merged))
                    consumed.update({hi, ti})
                    break
        for n_sec, merged in merged_texts:
            synthetic = _NoteCluster(text=merged + ".", forced=False)
            candidates.append((n_sec, synthetic))
        merged_norms = {_norm_cluster_text(m) for _, m in merged_texts}
        long_fragments = [
            (n, c) for n, c in long_fragments if _norm_cluster_text(c.text) not in merged_norms
        ]
        holders = {_norm_cluster_text(s.title): i for i, s in enumerate(sections) if s.title}
        untitled = [i for i, s in enumerate(sections) if s.title is None]
        if not untitled:
            return
        # The steal pool spans ALL clusters, including ones already held by
        # an unconfirmed neighbour: the classic failure is an off-by-one
        # arbitration that put the true owner's note on the next section.
        steal_pool: list[tuple[int, _NoteCluster]] = list(candidates)
        for event, clusters in prepared:
            for cluster in clusters:
                text = cluster.text.rstrip(".")
                norm = _norm_cluster_text(text)
                holder = holders.get(norm)
                if (
                    holder is not None
                    and not sections[holder].title_confident
                    and not self._is_fragment_text(text)
                ):
                    steal_pool.append((event.n_sections, cluster))
        for n_sec, cluster in steal_pool:
            norm = _norm_cluster_text(cluster.text.rstrip("."))
            holder = holders.get(norm)
            if holder is None or sections[holder].title_confident:
                continue
            for si in untitled:
                if si == holder or abs(n_sec - (si + 1)) > 2:
                    continue
                m = _title_match(cluster.text, sections[si], weights)
                h = _title_match(cluster.text, sections[holder], weights)
                if not (m.coverage >= 0.8 and m.distinctive):
                    continue
                margin = m.coverage - h.coverage
                # Content tie: when the note matches BOTH sections equally,
                # prefer the EARLIER (untitled) section — notes flush in
                # document order, so a tie means arbitration shifted the
                # note one section late. Only steal when the vacated holder
                # has a replacement candidate nearby, so the steal cannot
                # create a new missing title.
                if margin >= 0.2 or (
                    margin == 0
                    and si < holder
                    and n_sec <= holder + 1
                    and any(
                        abs(hn - (holder + 1)) <= 2
                        and _title_match(bc.text, sections[holder], weights).coverage >= 0.5
                        for hn, bc in candidates
                    )
                ):
                    sections[holder].title = None
                    sections[holder].title_confident = False
                    sections[si].title = cluster.text.rstrip(".")
                    sections[si].title_confident = False
                    break
        untitled = [i for i, s in enumerate(sections) if s.title is None]
        if not untitled:
            return
        # Pair scores: (section, cluster) -> coverage.
        all_candidates = candidates + long_fragments
        scores: dict[tuple[int, int], float] = {}
        distinctive: set[tuple[int, int]] = set()
        positional_strict: set[tuple[int, int]] = set()
        for si in untitled:
            for ci, (n_sec, cluster) in enumerate(all_candidates):
                if abs(n_sec - (si + 1)) > 2:
                    continue
                match = _title_match(cluster.text, sections[si], weights)
                scores[(si, ci)] = match.coverage
                if match.distinctive:
                    distinctive.add((si, ci))
                # Long lowercase fragments are admitted only on a
                # distinctive match at full coverage, flush-adjacent:
                # near-complete notes, never generic body prose.
                if ci >= len(candidates) and abs(n_sec - (si + 1)) <= 1 and match.coverage >= 0.6:
                    positional_strict.add((si, ci))
        # Greedy best-first assignment with a discrimination margin.
        pairs = sorted(
            (
                (cov, si, ci)
                for (si, ci), cov in scores.items()
                if (
                    ci < len(candidates)
                    and cov >= 0.3
                    and (
                        (si, ci) in distinctive
                        or self._sweep_positional_ok(cov, si, ci, all_candidates)
                    )
                )
                or ((si, ci) in positional_strict)
            ),
            reverse=True,
        )
        taken_sections: set[int] = set()
        taken_clusters: set[int] = set()
        for cov, si, ci in pairs:
            if si in taken_sections or ci in taken_clusters:
                continue
            rivals = [
                (scores.get((other, ci), 0.0), other)
                for other in untitled
                if other != si and other not in taken_sections
            ]
            best_rival = max(rivals, default=(0.0, -1))
            if cov - best_rival[0] < 0.15 and not (
                # Exact content tie: the EARLIER section wins — notes flush
                # in document order, so a tie means the neighbour stole it.
                cov == best_rival[0] and si < best_rival[1]
            ):
                continue
            n_sec, cluster = all_candidates[ci]
            sections[si].title = cluster.text.rstrip(".")
            sections[si].title_confident = False
            taken_sections.add(si)
            taken_clusters.add(ci)

    @staticmethod
    def _sweep_positional_ok(
        cov: float, si: int, ci: int, candidates: list[tuple[int, _NoteCluster]]
    ) -> bool:
        """Non-distinctive match allowed when position alone is strong.

        Some real notes share only common words with their section
        ("Enhanced punishment for certain offences after previous
        conviction" vs a body about convictions and offences). When the
        cluster flushed within one section of this section AND beats every
        rival by a wide coverage margin, the assignment stands — still
        flagged title_confident=False for review.
        """
        n_sec, _ = candidates[ci]
        return abs(n_sec - (si + 1)) <= 1 and cov >= 0.35

    @staticmethod
    def _dedupe_clusters(clusters: list[_NoteCluster]) -> list[_NoteCluster]:
        """Drop clusters whose text duplicates another cluster in this flush.

        The flat layer can capture the same printed margin word twice —
        once as note text ("Hurt.") and once re-split out of the body
        stream ("hurt.", "exposure."). Marginal notes are unique per
        flush: a cluster whose normalized text equals or is contained in
        another cluster's is the duplicate, not a second section's title.
        """
        norms = [_norm_cluster_text(c.text) for c in clusters]
        keep: list[int] = []
        for i, norm in enumerate(norms):
            if not norm:
                continue
            dup = False
            for j in range(len(norms)):
                if j == i or not norms[j]:
                    continue
                if norm == norms[j]:
                    # Text twins: keep the first printed (the TitleCase
                    # margin line, not its body-stream re-capture).
                    dup = i > j
                    break
                if norm in norms[j]:
                    dup = True
                    break
            if not dup:
                keep.append(i)
        return [clusters[i] for i in keep]

    @staticmethod
    def _is_body_echo(cluster: _NoteCluster, sections: list[Section], event: _FlushEvent) -> bool:
        """A lowercase-start cluster that repeats a body sentence tail.

        Some note-looking captures are body words the flat layer emitted
        on their own line ("restrain that person.", "commit an such
        person himself."). Marginal notes are TitleCase noun phrases; a
        lowercase-start cluster whose whole normalized text appears
        verbatim inside a flush-adjacent section's body is a body echo,
        not a title. TitleCase clusters are exempt — real notes often
        quote their section's body words ("Exposure and abandonment...").
        """
        text = cluster.text
        if not text or text[:1].isupper():
            return False
        norm = _norm_cluster_text(text)
        if not norm:
            return False
        lo = max(0, event.n_sections - 2)
        hi = min(len(sections), event.n_sections + 2)
        return any(norm in _norm_cluster_text(section.text) for section in sections[lo:hi])

    def _merge_split_clusters(self, clusters: list[_NoteCluster]) -> list[_NoteCluster]:
        """Rejoin clusters split mid-note inside one flush.

        A complete marginal note ends on terminal punctuation. A cluster
        that ends WITHOUT a terminal and is followed by a LOWERCASE-start
        cluster was split mid-phrase by the capture rules ("Right of
        private defence against" + "deadly assault when there is risk of
        harm to innocent person") — the pieces are one note. A following
        TitleCase-start cluster is a different section's note ("Voyeurism"
        + "Stalking.") and must stay separate.
        """
        merged: list[_NoteCluster] = []
        for cluster in clusters:
            if (
                merged
                and not merged[-1].text.rstrip(",;:").endswith(_TERMINAL)
                and cluster.text[:1].islower()
            ):
                merged[-1] = _NoteCluster(
                    f"{merged[-1].text} {cluster.text}",
                    merged[-1].forced or cluster.forced,
                )
            else:
                merged.append(cluster)
        return merged

    def _append_continuations(
        self,
        clusters: list[_NoteCluster],
        sections: list[Section],
        event: _FlushEvent,
        weights: dict[str, float],
    ) -> list[_NoteCluster]:
        """Fold a lowercase continuation fragment into the title it completes.

        A note split across a boundary flushes its tail ("likely to spread
        infection of disease dangerous to life", after "Malignant act") as
        a separate lowercase-start cluster. Marginal notes are TitleCase,
        so such a fragment extends the flush-adjacent section's
        unconfirmed title. The title must currently END DANGLING (on a
        connective — "child under", "private defence against"): that is
        the signal the note was cut mid-phrase. The fragment's own tokens
        must describe the section, and gluing must not dilute the match
        (a neighbour's fragment would). Anything that does not fit stands
        on its own.
        """
        rest: list[_NoteCluster] = []
        idx = event.n_sections - 1
        target = sections[idx] if 0 <= idx < len(sections) else None
        for cluster in clusters:
            if target is not None and target.title and not target.title_confident:
                title_norm = _norm_cluster_text(target.title)
                frag_norm = _norm_cluster_text(cluster.text)
                last_word = title_norm.rsplit(maxsplit=1)[-1] if title_norm else ""
                own = _title_match(cluster.text, target, weights).coverage
                before = _title_match(target.title, target, weights).coverage
                merged = f"{target.title} {cluster.text.rstrip('.')}"
                after = _title_match(merged, target, weights).coverage
                if (
                    cluster.text[:1].islower()
                    and frag_norm
                    and frag_norm not in title_norm
                    and last_word in _DANGLING_CONNECTIVES
                    and own >= 0.6
                    and after >= before - 0.02
                ):
                    target.title = merged
                    continue
            rest.append(cluster)
        return rest

    def _window_assign(
        self,
        clusters: list[_NoteCluster],
        sections: list[Section],
        event: _FlushEvent,
        weights: dict[str, float],
    ) -> bool:
        """Order-preserving content matching for multi-cluster flushes.

        The flat layer can emit several sections' notes together at one
        boundary (page 14: both s.26's and s.27's notes precede s.26's
        header), and a cluster may equally belong to the section AFTER
        the boundary. The clusters are matched as an ordered run against
        the untitled sections in a window around the boundary; content
        picks the run, position only breaks ties. A backlog of untitled
        sections from BEFORE the window must not be bypassed — the caller
        falls back to the backlog fill. Returns False when no run is
        content-plausible.
        """
        n = event.n_sections
        lo = max(0, n - max(2, len(clusters)))
        pool = [
            (i, sections[i])
            for i in range(lo, min(len(sections), n + 3))
            if sections[i].title is None
        ]
        if not pool:
            return False
        default_idx = n if event.kind == "header" else n - 1
        # Match every cluster against every pool section once.
        matches: list[list[_TitleMatch]] = [
            [_title_match(c.text, s, weights) for _, s in pool] for c in clusters
        ]
        assign_bonus = 0.05

        def value(ci: int, pi: int) -> float | None:
            """Pairing value of cluster ci with pool entry pi.

            The pair floor is the old run floor; the margin demands the
            pair beat the same cluster's best pairing elsewhere in the
            pool. That drops junk body fragments ("restrain that person.",
            "that person.") which match every section equally, while real
            titles discriminate. A TitleCase cluster gets the benefit of
            the doubt at margin 0 (a lone "Hurt." title); a lowercase
            cluster must discriminate to be assignable at all.
            """
            m = matches[ci][pi]
            if not (m.coverage >= 0.25 and (m.distinctive or m.coverage >= 0.65)):
                return None
            rival_best = (
                max(matches[ci][q].coverage for q in range(len(pool)) if q != pi)
                if len(pool) > 1
                else 0.0
            )
            margin = m.coverage - rival_best
            if clusters[ci].text[:1].islower() and margin <= 0.01:
                return None
            return max(0.0, margin) + assign_bonus

        # Monotone partial alignment (order-preserving; clusters and pool
        # sections may both be skipped) via DP over (cluster, pool pos).
        # Outcome tuples compare lexicographically: total value, then
        # assignments made, then negated total distance from the
        # positional default, then negated total pool position (earlier
        # alignment wins exact ties — a one-word title like "Hurt." is a
        # content tie across neighbours and position must resolve it
        # toward the boundary). Content picks the alignment; position
        # only breaks ties.
        nc, npool = len(clusters), len(pool)
        dp: list[list[tuple[float, int, float, float]]] = [
            [(0.0, 0, 0.0, 0.0)] * (npool + 1) for _ in range(nc + 1)
        ]
        choice: list[list[int | str | None]] = [[None] * (npool + 1) for _ in range(nc + 1)]
        for ci in range(nc - 1, -1, -1):
            dp[ci][npool] = dp[ci + 1][npool]  # pool exhausted: skip cluster
            choice[ci][npool] = "skip_cluster"
            for pi in range(npool - 1, -1, -1):
                best: tuple[float, int, float, float] = dp[ci][pi + 1]
                ch: int | str = "skip_pool"
                if dp[ci + 1][pi] > best:
                    best, ch = dp[ci + 1][pi], "skip_cluster"
                v = value(ci, pi)
                if v is not None:
                    sub = dp[ci + 1][pi + 1]
                    cand = (
                        sub[0] + v,
                        sub[1] + 1,
                        sub[2] - abs(pool[pi][0] - default_idx),
                        sub[3] - pi,
                    )
                    if cand > best:
                        best, ch = cand, pi
                dp[ci][pi] = best
                choice[ci][pi] = ch
        if dp[0][0][1] == 0:
            return False
        ci = pi = 0
        while ci < nc:
            step = choice[ci][pi]
            if step == "skip_pool":
                pi += 1
            elif step == "skip_cluster":
                ci += 1
            elif isinstance(step, int):
                cluster = clusters[ci]
                _, section = pool[step]
                section.title = cluster.text.rstrip(".")
                section.title_confident = not cluster.forced and _decisive(matches[ci][step], None)
                ci += 1
                pi = step + 1
        return True

    def _assign_last(
        self,
        cluster: _NoteCluster,
        sections: list[Section],
        default_idx: int,
        other_idx: int,
        weights: dict[str, float],
        event: _FlushEvent,
    ) -> None:
        """Place a flush event's last cluster: content first, position second.

        Ownership rules, in order:

        1. An untitled positional default takes the cluster unless an
           untitled challenger describes it strictly better (coverage,
           then rarity-weighted strength); the default wins exact ties.
           Adjacent sections in a topic cluster (the coin/stamp run)
           share vocabulary, so "strictly better" — not "clearly better" —
           is the bar: demanding a solid coverage edge would gift the
           note to the default neighbour.
        2. An untitled challenger takes it when the default already owns a
           title and the challenger has decisive content evidence.
        3. An unconfirmed (title_confident=False) default title may be
           REPLACED: clusters captured inside the section's text stream
           (inner, interleaved flushes) have a strong positional claim, and
           a clearly better description beats a weak existing one.
        4. Confidently titled sections never lose their note; a cluster no
           neighbour claims with any evidence is margin junk and drops.

        Confidence requires decisive content evidence; everything else is
        flagged for review rather than guessed.
        """

        def at(idx: int) -> Section | None:
            return sections[idx] if 0 <= idx < len(sections) else None

        default, other = at(default_idx), at(other_idx)
        # A confidently titled section already owns its note; yield it.
        if default is not None and default.title is not None and default.title_confident:
            default = None
        if other is not None and other.title is not None and other.title_confident:
            other = None
        if default is None and other is None:
            # Both boundary neighbours already own confident titles, but a
            # note flushed one section LATE (its section's header consumed
            # a glued fragment, so the cluster flushed after the next
            # section started) still has a rightful owner at n-2. Only a
            # decisive content match claims it — never positional guess.
            back = at(default_idx - 1)
            if back is not None and back.title is None:
                bm = _title_match(cluster.text, back, weights)
                if _decisive(bm, None):
                    back.title = cluster.text.rstrip(".")
                    back.title_confident = not cluster.forced and _decisive(bm, None)
            return

        default_open = default is not None and default.title is None
        other_open = other is not None and other.title is None

        if default_open and default is not None:
            dm = _title_match(cluster.text, default, weights)
            target: Section = default
            win: _TitleMatch = dm
            lose: _TitleMatch | None = None
            if other_open and other is not None:
                om = _title_match(cluster.text, other, weights)
                lose = om
                if _match_key(om) > _match_key(dm):
                    target, win = other, om
            target.title = cluster.text.rstrip(".")
            target.title_confident = not cluster.forced and _decisive(win, lose)
            return

        if other_open and other is not None:
            om = _title_match(cluster.text, other, weights)
            dfl = _title_match(cluster.text, default, weights) if default is not None else None
            if om.coverage > 0 and (dfl is None or not _clearly_better(dfl, om)):
                other.title = cluster.text.rstrip(".")
                other.title_confident = not cluster.forced and _decisive(om, dfl)
                return

        if default is not None and default.title is not None:
            # The default carries an unconfirmed title: this cluster may
            # correct it. Inner (mid-section) captures sit inside the
            # section's own text stream; otherwise only a clearly better
            # description replaces what is already there.
            dm = _title_match(cluster.text, default, weights)
            old = _title_match(default.title, default, weights)
            if dm.coverage >= 0.5 and (
                (event.inner and cluster.forced) or _clearly_better(dm, old)
            ):
                default.title = cluster.text.rstrip(".")
                default.title_confident = False if cluster.forced else _decisive(dm, old)
                return

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

    def _is_note_run_start(self, line: str, next_line: str | None) -> bool:
        """Whether a sentence-starter line may open a marginal-note run.

        Marginal notes often share their opening words with the section
        body ("Every member of unlawful assembly guilty..."), so the
        sentence-starter exclusion must not swallow note heads. A short
        sentence-starter line is a note run start when the NEXT line is
        itself a short note-like line — a body sentence wrap is followed
        by more body text (long lines), never by a run of short
        title-case/lowercase fragments ending at a boundary.
        """
        if not next_line or not next_line.strip():
            return False
        if len(next_line) > _NOTE_LINE_MAX:
            return False
        if (
            SECTION_RE.match(next_line)
            or SUBSECTION_RE.match(next_line)
            or CLAUSE_RE.match(next_line)
            or PROVISO_RE.match(next_line)
            or EXPLANATION_RE.match(next_line)
            or ILLUSTRATION_RE.match(next_line)
            or EXCEPTION_RE.match(next_line)
            or CHAPTER_RE.match(next_line)
        ):
            return False
        return self._is_note_line(next_line)

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

    def _is_semicolon_note_continuation(self, line: str) -> bool:
        """A short lowercase fragment with INTERNAL ";" or ":" that
        continues an open note cluster ("committed; if not committed").

        Only valid while a cluster is already open: standalone such lines
        are body punctuation, and allowing them to OPEN a run lets short
        body fragments pollute the margin stream.
        """
        if len(line) > _NOTE_LINE_MAX:
            return False
        if ";" not in line and ":" not in line:
            return False
        return bool(re.match(r"^[a-z][a-z0-9,()' ;:-]*$", line))

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
