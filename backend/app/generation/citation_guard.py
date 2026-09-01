"""Citation guard (REQUIREMENTS A4-001..A4-016; ARCHITECTURE §18-§19).

Executable post-generation validation — not prompt-only (A4-016). Extracts
citations from generated text, verifies each cited Act/section (or user
document page) against the retrieved evidence, and strips invalid or
irrelevant citations (with the sentences that depend on them) rather than
trusting model output.

Citation forms (A4-005, A5-008)::

    [BNS s.103(1)]   [BNS s.103]   [BNSS s.197(3)]   [Document d31f... p.2]

Validation is layered, and every layer must pass:

1. **Existence** — the cited (act, section) pair (or document id/page)
   must be present in the retrieved evidence. Otherwise the sentence is
   removed: stripping the citation alone would leave an unsupported legal
   claim.
2. **Granularity** — a citation with a subsection must match a retrieved
   chunk of that subsection, or a whole-section chunk whose text covers
   the section including its subsections.
3. **Relevance** — the sentence must share at least one content token
   with the cited chunk. A citation attached to a sentence about the
   assistant itself ("I am Nyaya") or to a sentence with no substantive
   content is decorative, never evidence for a claim: the citation is
   stripped and the sentence kept only when it makes no legal claim.

The Act short code in a statute citation must match the evidence's
act_short; citations never cross corpora.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, Field

from app.documents.models import DocumentHit
from app.retrieval.models import ScoredChunk

# [BNS s.103(1)] / [BNSS s.197] — act short code, section, optional subsections
CITATION_RE = re.compile(r"\[([A-Za-z]{2,6})\s+s\.(\d{1,3})((?:\(\d{1,3}\)){0,2})\]")

# A bracket that LOOKS like a statute citation (act short code + "s.<digit>")
# but does not match CITATION_RE exactly, e.g. "[BNS s.103 p.29-30]" — a
# malformed label produced by the model. It cannot be validated, so leaving
# it in place would let an unvalidated citation pass as evidence. Any such
# bracket is treated as an invalid citation (sentence removed). The same
# applies to parenthesized citation-shaped labels like "(BNSS s.234)".
_MALFORMED_STATUTE_CITATION_RE = re.compile(r"\[([A-Za-z]{2,6})\s+s\.(\d{1,3})[^\]]*\]")
_PAREN_STATUTE_CITATION_RE = re.compile(r"\(([A-Za-z]{2,6})\s+s\.(\d{1,3})[^\)]*\)")

# [Document d31f9c p.2] / [Document d31f9c] — user-document citations (A5-008).
# Small models frequently drop the literal "Document" word or brace the id
# ("[d31f9c... p.1]", "[{d31f9c...} p.1]"); the bare-id form is accepted for
# hex-shaped ids only — document ids are hex — so a statute bracket can
# never be misread as a document citation. Every extracted id is still
# validated against the session's retrieved documents below.
DOCUMENT_CITATION_RE = re.compile(
    r"\[Document\s+(\S+?)(?:\s+p\.(\d{1,4}))?\]"
    r"|\[\{?([0-9a-fA-F]{8,64})\}?(?:\s+p\.(\d{1,4}))?\]"
)
# Bare-id branch of DOCUMENT_CITATION_RE, standalone (for normalization).
_BARE_DOCUMENT_RE = re.compile(r"\[\{?([0-9a-fA-F]{8,64})\}?(?:\s+p\.(\d{1,4}))?\]")
# "[Document {<id>} p.1]" — id braced inside the label (normalization only).
_CURLY_DOC_LABEL_RE = re.compile(r"\[Document\s+\{([0-9a-fA-F]{8,64})\}(?:\s+p\.(\d{1,4}))?\]")

# Section numbers cited in prose, e.g. "section 103 of BNS says ..."
# Only used for the unsupported-claim check, never to mint new citations.
PROSE_CITATION_RE = re.compile(
    r"\bsections?\s+(\d{1,3})(?:\s*\(\s*(\d{1,3})\s*\))?(?:\s+of\s+)?\s*(?:the\s+)?"
    r"(BNS|BNSS)?",
    re.IGNORECASE,
)

# Prose section references in the supported Indian languages (D-077):
# "धारा 103 कहती है", "ধারা ১০৩ বলে", "பிரிவு 103", ... Act labels remain
# English-only in prose. Only used for the unsupported-claim check.
_INDIC_SECTION_WORDS = "धारा|ধারা|કલમ|பிரிவு|సెక్షన్|ವಿಧಿ|ಸೆಕ್ಷನ್|വകുപ്പ്|ਧਾਰਾ|ਸੈਕਸ਼ਨ|ଧାରା|दफ़ा|दफा"
_INDIC_PROSE_RE = re.compile(
    rf"(?:{_INDIC_SECTION_WORDS})\s*(\d{{1,3}})(?:\s*\(\s*(\d{{1,3}})\s*\))?"
)

# Indic digit forms (all supported scripts, D-077) normalized to ASCII
# before matching, so "धारा १०३", "ধারা ১০৩", "કલમ ૧૦૩" and "ଧାରା ୧୦୩"
# are the same claim as "धारा 103".
_INDIC_DIGITS = {0x0966 + offset: str(offset) for offset in range(10)}  # Devanagari
_DIGIT_TRANSLATION = {
    **_INDIC_DIGITS,
    **{0x09E6 + offset: str(offset) for offset in range(10)},  # Bengali/Assamese
    **{0x0AE6 + offset: str(offset) for offset in range(10)},  # Gujarati
    **{0x0B66 + offset: str(offset) for offset in range(10)},  # Odia
}


# Act names that must NOT appear in an answer grounded in a different Act.
# The model sometimes writes "section 103 of the Indian Penal Code" while the
# evidence corpus is the BNS — a section number that exists in both statutes
# but means different law. The short code each name maps to is compared
# against the evidence's act set; a mismatch is a misattribution.
_ACT_NAME_ALIASES: dict[str, str] = {
    "indian penal code": "IPC",
    "ipc": "IPC",
    "code of criminal procedure": "CrPC",
    "crpc": "CrPC",
    "indian evidence act": "IEA",
    "indian penal sanhita": "IPS",
}
_ACT_NAME_RE = re.compile(
    r"\b(indian penal code|ipc|code of criminal procedure|crpc|indian evidence act)\b",
    re.IGNORECASE,
)

# Sentence-anchored legal-consequence vocabulary: a sentence using these
# words makes a substantive claim about the law (A4-016 — such a claim must
# carry a validated citation or a prose reference to an evidenced section).
# Indic equivalents cover the multilingual path (D-077).
_LEGAL_CLAIM_RE = re.compile(
    r"\b(punished|punishment|imprisonment|imprisoned|liable to|offence|offense|"
    r"fine|sentence|imprisonment for life|death sentence)\b"
    r"|दंड|सजा|कारावास|आजीवन|दोषी|अपराध|जुर्माना|দণ্ড|সাজা|কারাবাস|தண்டனை|சிறை",
    re.IGNORECASE,
)

#: The single-word vocabulary of ``_LEGAL_CLAIM_RE``. An uncited legal claim
#: adjacent to a cited sentence shares these words with EVERY penalty section,
#: so they cannot by themselves prove the claim is traceable to the cited
#: chunk: sibling-citation grounding requires at least one substantive
#: (non-claim-vocabulary) shared token in addition to the overlap threshold.
_CLAIM_VOCABULARY = frozenset(
    {
        "punished",
        "punishment",
        "imprisonment",
        "imprisoned",
        "liable",
        "offence",
        "offense",
        "fine",
        "sentence",
        "death",
    }
)

#: Minimum shared content tokens between an uncited claim and the cited
#: chunk for a trailing sibling citation to ground the claim.
_SIBLING_MIN_SHARED_TOKENS = 2


def _ascii_digits(text: str) -> str:
    """Map Indic-script digits (all supported scripts) to ASCII."""
    return text.translate(_DIGIT_TRANSLATION)


# First-person / assistant-identity vocabulary: a sentence containing these
# talks about the assistant, not the law, so it can never be evidence for a
# citation. "i.e." / "e.g." are removed before the test so their "i" does
# not trigger it. Indic first-person and "Nyaya" words are included so a
# translated identity sentence is treated the same way (D-077).
# Possessive/object "my"/"me" are deliberately EXCLUDED: a grounded answer
# echoing the user's first-person phrasing ("my bail", "helps me") would
# otherwise lose a perfectly relevant citation. Second-person forms are
# excluded for the same reason (Tamil "நீங்கள்" = "you" — user-facing).
#
# The brand noun ("Nyaya"/"न्याय"/...) is NOT a marker by itself (D-095):
# it is part of the corpus act's own name (Bharatiya Nyaya Sanhita) and
# the ordinary word for "justice" — a grounded answer naming the act in
# any language must keep its citations. Identity sentences still match
# through their first-person pronouns ("मैं न्याय हूँ", "I am Nyaya").
# Script tokens use Indic-block lookaround boundaries so a pronoun inside
# a longer word (Marathi "मी" inside "समीक्षा") is not a match.
_SELF_REFERENCE_RE = re.compile(
    r"\b(?:i|i'm|im|myself|we|our|assistant|chatbot|robot|bot|ai)\b"
    r"|(?<![ऀ-ൿ])(?:मैं|आम्ही|हुँ|हुं|હું|আমি|মই|நான்|నేను|ನಾನು|ഞാൻ|ਮੈਂ|ମୁଁ)"
    r"(?![ऀ-ൿ])",
    re.IGNORECASE,
)
_ABBREVIATION_RE = re.compile(r"\b(?:i\.e|e\.g)\b\.?", re.IGNORECASE)

_WORD_RE = re.compile(r"[a-z0-9]+")

# Words that carry no claim content: function words plus common reporting
# verbs. A citation is relevant only when the sentence shares at least one
# remaining (content) token with the cited chunk.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "and",
        "or",
        "but",
        "not",
        "no",
        "nor",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "as",
        "at",
        "by",
        "from",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "done",
        "shall",
        "may",
        "might",
        "can",
        "could",
        "will",
        "would",
        "should",
        "must",
        "also",
        "such",
        "other",
        "any",
        "all",
        "each",
        "every",
        "some",
        "there",
        "here",
        "when",
        "where",
        "why",
        "how",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "if",
        "then",
        "than",
        "so",
        "very",
        "more",
        "most",
        "much",
        "many",
        "few",
        "both",
        "between",
        "under",
        "above",
        "below",
        "into",
        "upon",
        "about",
        "against",
        "during",
        "before",
        "after",
        "over",
        "again",
        "further",
        "he",
        "she",
        "they",
        "them",
        "him",
        "her",
        "his",
        "hers",
        "theirs",
        "you",
        "your",
        "yours",
        "we",
        "us",
        "our",
        "ours",
        "me",
        "my",
        "mine",
        "myself",
        "itself",
        "themselves",
        "yourself",
        "says",
        "said",
        "say",
        "provides",
        "provide",
        "states",
        "state",
        "stated",
        "according",
        "per",
        "section",
        "sections",
        "act",
        "acts",
    ]
)


class Citation(BaseModel):
    """One extracted statute citation."""

    act_short: str
    section_number: str
    subsections: str = ""  # e.g. "(1)" or "(3)(a)"-style raw match

    @property
    def label(self) -> str:
        return f"[{self.act_short} s.{self.section_number}{self.subsections}]"


class DocumentCitation(BaseModel):
    """One extracted user-document citation."""

    document_id: str
    page: int | None = None

    @property
    def label(self) -> str:
        if self.page is None:
            return f"[Document {self.document_id}]"
        return f"[Document {self.document_id} p.{self.page}]"


class CitationCheck(BaseModel):
    """Result of validating a generated answer against evidence."""

    valid_citations: list[Citation] = Field(default_factory=list)
    invalid_citations: list[Citation] = Field(default_factory=list)
    # Sentences carrying an invalid citation or an unsupported prose section
    # claim, removed from the answer.
    removed_sentences: list[str] = Field(default_factory=list)
    # Prose section references with no supporting citation/evidence.
    uncited_section_claims: list[str] = Field(default_factory=list)
    citations_removed: int = 0
    # Citation labels stripped from kept sentences: they existed in the
    # evidence but did not support the sentence they were attached to
    # (self-referential or content-free sentences), or the sentence shared
    # no content token with the cited chunk.
    irrelevant_citations: list[str] = Field(default_factory=list)
    # Subsection citations whose section exists in evidence but whose
    # subsection does not (granularity failures, A4-004).
    subsection_mismatches: list[str] = Field(default_factory=list)
    # User-document citations that reference an uncited/unretrieved document.
    invalid_document_citations: list[str] = Field(default_factory=list)
    # Document ids whose citations validated (drives source filtering).
    cited_document_ids: list[str] = Field(default_factory=list)
    # Document citations on non-Latin sentences that passed existence and
    # page validation without a lexical-overlap check: cross-script token
    # overlap is structurally impossible (Hindi sentence, English document),
    # so relevance is waived there rather than falsely refused (D-077).
    relevance_waived: int = 0
    # Sentences naming a different Act than the evidence corpus (e.g.
    # "Indian Penal Code" in a BNS answer) — removed as misattributions.
    misattributed_act_sentences: list[str] = Field(default_factory=list)
    # Citation-free sentences making a legal-consequence claim (punishment
    # vocabulary with no citation and no evidenced section reference).
    uncited_legal_claims: list[str] = Field(default_factory=list)


def _normalize_brackets(text: str) -> str:
    """Drop stray braces small models add around citation labels.

    "[{Document <id> p.1}]" / "[{<id>} p.1]" → "[Document <id> p.1]". The
    canonical citation contract contains no braces, so this rewrite can
    never alter a well-formed label; it only recovers a mangled one.
    """
    return text.replace("[{", "[").replace("}]", "]").replace("[ ", "[").replace(" ]", "]")


def extract_citations(text: str) -> list[Citation]:
    """Extract bracket citations from generated text, in order."""
    return [
        Citation(
            act_short=match.group(1).upper(),
            section_number=match.group(2),
            subsections=match.group(3) or "",
        )
        for match in CITATION_RE.finditer(_normalize_brackets(text))
    ]


def extract_document_citations(text: str) -> list[DocumentCitation]:
    """Extract user-document bracket citations from generated text."""
    citations: list[DocumentCitation] = []
    for match in DOCUMENT_CITATION_RE.finditer(_normalize_brackets(text)):
        if match.group(1) is not None:  # "[Document <id> [p.N]]" form
            document_id, page = match.group(1), match.group(2)
        else:  # bare hex-id form: "[<id> [p.N]]" / "[{<id>} p.N]"
            document_id, page = match.group(3), match.group(4)
        # "[Document {<id>} p.1]" — braces wrapped around the id INSIDE the
        # label (not covered by _normalize_brackets). The braces are
        # formatting noise; the id itself is still fully validated below.
        document_id = document_id.strip("{}")
        citations.append(DocumentCitation(document_id=document_id, page=int(page or 0) or None))
    return citations


def _malformed_statute_citations(sentence: str, valid: list[Citation]) -> list[Citation]:
    """Citation-shaped labels that do not match the exact citation contract.

    ``[BNS s.103 p.29-30]`` looks like a statute citation but is not the
    ``[BNS s.103]`` form, so it cannot be validated; it is reported as an
    invalid citation rather than silently passing as prose.
    """
    valid_labels = {citation.label for citation in valid}
    malformed: list[Citation] = []
    for regex in (_MALFORMED_STATUTE_CITATION_RE, _PAREN_STATUTE_CITATION_RE):
        for match in regex.finditer(sentence):
            if CITATION_RE.fullmatch(match.group(0)):
                continue  # exact contract form — not malformed
            citation = Citation(act_short=match.group(1).upper(), section_number=match.group(2))
            if citation.label not in valid_labels:
                malformed.append(citation)
    return malformed


def _content_tokens(text: str) -> set[str]:
    """Lowercase content tokens: alphabetic, non-stopword."""
    return {
        token
        for token in _WORD_RE.findall(text.lower())
        if token.isalpha() and token not in _STOPWORDS
    }


def _has_indic_content(text: str) -> bool:
    """True when the text carries Indic-script letters (not digits/punct).

    Used only to select the multilingual relevance path (D-077): Latin
    sentences keep the exact pre-multilingual checks.
    """
    for char in text:
        if 0x0900 <= ord(char) <= 0x0D7F and unicodedata.category(char).startswith("L"):
            return True
    return False


def _has_section_number(text: str, section: str) -> bool:
    """True when the (digit-normalized) text contains the section number."""
    return re.search(rf"(?<!\d){re.escape(section)}(?!\d)", _ascii_digits(text)) is not None


def _split_sentences(text: str) -> list[str]:
    """Split into sentences, keeping terminal punctuation and trailing whitespace.

    Terminal punctuation covers ASCII ``.!?`` and the Indic danda
    ``।``/``॥`` (U+0964/U+0965, used across Devanagari- and Bengali-script
    languages). Without the danda, a multi-sentence Indic answer is one
    giant "sentence" and a single invalid citation or unsupported prose
    claim would remove the entire answer (D-077 regression).
    """
    parts = re.split(r"(?<=[.!?।॥])\s+", text)
    return [p for p in parts if p.strip()]


#: A fragment that is nothing but citation labels, e.g. "[BNS s.103]" or
#: "[{d31f9c...} p.1]" — small models frequently place the citation AFTER
#: the sentence-final period instead of inside the sentence.
_LABEL_ONLY_RE = re.compile(r"^(\[[^\]]*\]\s*)+$")


def _merge_label_fragments(sentences: list[str]) -> list[str]:
    """Attach label-only fragments to the sentence they cite.

    "... of 30 days. [{id} p.1]" splits into a claim sentence and a
    label-only fragment; without this merge the fragment's citation is
    discarded as decorative and a perfectly grounded answer loses its only
    citation. Only a fragment with a predecessor is merged; a leading
    label-only fragment is left for the normal (discard) path.
    """
    merged: list[str] = []
    for sentence in sentences:
        if merged and _LABEL_ONLY_RE.match(sentence.strip()):
            merged[-1] = merged[-1].rstrip() + " " + sentence.strip() + " "
            continue
        merged.append(sentence)
    return merged


def _sentence_stream(text: str) -> list[tuple[str, bool]]:
    """Split into ``(sentence, starts_paragraph)`` pairs, in order.

    Paragraphs are blocks separated by a blank line; the flag marks the
    first sentence of each paragraph. Sibling-citation grounding must never
    cross a paragraph boundary — a citation in a new paragraph cites that
    paragraph, not the previous one. Label-only fragments merge into their
    predecessor exactly as in the flat split.
    """
    entries: list[tuple[str, bool]] = []
    for paragraph in re.split(r"\n\s*\n", text):
        for position, sentence in enumerate(_split_sentences(paragraph)):
            entries.append((sentence, position == 0))
    merged: list[tuple[str, bool]] = []
    for sentence, starts_paragraph in entries:
        if merged and _LABEL_ONLY_RE.match(sentence.strip()):
            previous, previous_starts = merged[-1]
            merged[-1] = (previous.rstrip() + " " + sentence.strip() + " ", previous_starts)
            continue
        merged.append((sentence, starts_paragraph))
    return merged


def _self_referential(sentence: str) -> bool:
    """True when the sentence is about the assistant, not the law."""
    return bool(_SELF_REFERENCE_RE.search(_ABBREVIATION_RE.sub("", sentence)))


def _strip_labels(sentence: str, labels: list[str]) -> str:
    """Remove citation labels and tidy the leftover spacing/punctuation."""
    for label in labels:
        sentence = sentence.replace(label, "")
    sentence = re.sub(r"\s{2,}", " ", sentence)
    sentence = re.sub(r"\s+([.,;:!?।॥])", r"\1", sentence)
    return sentence.strip()


class _EvidenceIndex:
    """Precomputed views over the retrieved evidence for validation."""

    def __init__(
        self,
        evidence: list[ScoredChunk],
        document_hits: list[DocumentHit] | None = None,
    ) -> None:
        self.by_key: dict[tuple[str, str], list[ScoredChunk]] = {}
        for scored in evidence:
            self.by_key.setdefault(
                (scored.chunk.act_short, scored.chunk.section_number), []
            ).append(scored)
        self.keys = set(self.by_key)
        self.acts = {act for act, _section in self.keys}
        self.sections = {section for _act, section in self.keys}
        self.document_hits_by_id: dict[str, list[DocumentHit]] = {}
        for hit in document_hits or []:
            self.document_hits_by_id.setdefault(hit.document_id, []).append(hit)
        self._token_cache: dict[str, set[str]] = {}

    def statute_supported(self, citation: Citation) -> bool:
        """Existence + subsection-granularity check.

        A subsection citation is covered by a chunk of that exact
        subsection, or by a whole-section chunk whose verbatim text
        contains the subsection marker.
        """
        chunks = self.by_key.get((citation.act_short, citation.section_number))
        if not chunks:
            return False
        if not citation.subsections:
            return True
        return bool(self._subsection_candidates(chunks, citation.subsections))

    @staticmethod
    def _subsection_candidates(chunks: list[ScoredChunk], subsections: str) -> list[ScoredChunk]:
        return [
            scored
            for scored in chunks
            if scored.chunk.subsection == subsections
            or (scored.chunk.subsection is None and subsections in (scored.chunk.text or ""))
        ]

    def document_supported(self, citation: DocumentCitation) -> bool:
        hits = self.document_hits_by_id.get(citation.document_id)
        if not hits:
            return False
        if citation.page is None:
            return True
        return any(
            hit.page_start is not None
            and hit.page_end is not None
            and hit.page_start <= citation.page <= hit.page_end
            for hit in hits
        )

    def statute_tokens(self, citation: Citation) -> set[str]:
        key = f"s:{citation.act_short}:{citation.section_number}:{citation.subsections}"
        if key not in self._token_cache:
            chunks = self.by_key.get((citation.act_short, citation.section_number), [])
            candidates = (
                self._subsection_candidates(chunks, citation.subsections)
                if citation.subsections
                else chunks
            )
            merged: set[str] = set()
            for scored in candidates:
                chunk = scored.chunk
                merged |= _content_tokens(f"{chunk.section_title or ''} {chunk.text}")
            self._token_cache[key] = merged
        return self._token_cache[key]

    def document_tokens(self, citation: DocumentCitation) -> set[str]:
        key = f"d:{citation.document_id}:{citation.page}"
        if key not in self._token_cache:
            merged: set[str] = set()
            for hit in self.document_hits_by_id.get(citation.document_id, []):
                if citation.page is not None and not (
                    hit.page_start is not None
                    and hit.page_end is not None
                    and hit.page_start <= citation.page <= hit.page_end
                ):
                    continue
                merged |= _content_tokens(hit.text)
            self._token_cache[key] = merged
        return self._token_cache[key]


def _prose_gate(sentence: str, index: _EvidenceIndex, check: CitationCheck) -> str | None:
    """Remove citation-free sentences that make unsupported section claims.

    A prose reference ("section 999 of BNS says ...", "धारा 999 कहती है")
    is supported when the cited section exists in the retrieved evidence —
    with or without an Act label. Unsupported prose claims are removed with
    their sentence; the flag is recorded whether or not the sentence
    survives.
    """
    claims: list[str] = []
    for match in PROSE_CITATION_RE.finditer(sentence):
        act = (match.group(3) or "").upper() or None
        section = match.group(1)
        supported = (act, section) in index.keys if act else section in index.sections
        if not supported:
            claim = match.group(0).strip()
            claims.append(claim)
            if claim not in check.uncited_section_claims:
                check.uncited_section_claims.append(claim)
    # Indic-script prose claims (D-077): digit forms are normalized so
    # "धारा १०३" and "ধারা ১০৩" check against the same sections.
    ascii_sentence = _ascii_digits(sentence)
    for match in _INDIC_PROSE_RE.finditer(ascii_sentence):
        section = match.group(1)
        if section not in index.sections:
            claim = match.group(0).strip()
            claims.append(claim)
            if claim not in check.uncited_section_claims:
                check.uncited_section_claims.append(claim)
    if claims:
        check.removed_sentences.append(sentence.strip())
        return None
    return sentence


def _sibling_citation_grounds(
    sentences: list[tuple[str, bool]],
    position: int,
    claim_tokens: set[str],
    index: _EvidenceIndex,
) -> bool:
    """True when the citation on the immediately FOLLOWING sentence of the
    same paragraph also grounds THIS sentence's uncited legal claim.

    Live shape (BNS s.103 false positive): the model states the punishment
    rule without a citation and cites the section on the elaborating
    sentence that follows. The claim keeps its traceability contract only
    when it is actually traceable to the cited evidence — at least
    ``_SIBLING_MIN_SHARED_TOKENS`` shared content tokens with the cited
    chunk, of which at least one is substantive (not generic punishment
    vocabulary, which every penalty claim shares with every penalty
    section). Anything weaker is an unsupported claim parked next to a real
    citation and is still removed.
    """
    if position + 1 >= len(sentences):
        return False
    following, starts_paragraph = sentences[position + 1]
    if starts_paragraph or not claim_tokens:
        return False
    for citation in extract_citations(following):
        if not index.statute_supported(citation):
            continue
        shared = index.statute_tokens(citation) & claim_tokens
        if len(shared) >= _SIBLING_MIN_SHARED_TOKENS and shared - _CLAIM_VOCABULARY:
            return True
    for document_citation in extract_document_citations(following):
        if not index.document_supported(document_citation):
            continue
        shared = index.document_tokens(document_citation) & claim_tokens
        if len(shared) >= _SIBLING_MIN_SHARED_TOKENS and shared - _CLAIM_VOCABULARY:
            return True
    return False


def validate_citations(
    answer: str,
    evidence: list[ScoredChunk],
    document_hits: list[DocumentHit] | None = None,
) -> tuple[str, CitationCheck]:
    """Validate an answer's citations against the retrieved evidence.

    Returns ``(sanitized_answer, check)``. Sentences containing a citation
    whose (act, section) is absent from the evidence — or whose subsection
    is not covered by any retrieved chunk — are removed entirely. Citations
    that exist in the evidence but support no claim in their sentence
    (self-referential sentences, content-free sentences, zero lexical
    overlap with the cited chunk) are stripped from the kept sentence and
    reported as irrelevant. Document citations are validated the same way
    against the session's retrieved document hits.

    A citation on one sentence extends grounding to the immediately
    preceding sentence of the same paragraph (a legal claim stated without
    a citation and cited on the elaborating sentence that follows) only
    when the claim itself is traceable to the cited chunk — see
    ``_sibling_citation_grounds``. Anything weaker is still removed.
    """
    index = _EvidenceIndex(evidence, document_hits)
    check = CitationCheck()

    kept: list[str] = []
    sentences = _sentence_stream(_normalize_brackets(answer))
    for position, (sentence, _starts_paragraph) in enumerate(sentences):
        act_name = _ACT_NAME_RE.search(sentence)
        if act_name and _ACT_NAME_ALIASES[act_name.group(0).lower()] not in index.acts:
            # A named Act the evidence does not contain is a misattribution
            # even when the section number happens to exist in both
            # statutes (s.103 IPC ≠ s.103 BNS), with or without a citation.
            check.misattributed_act_sentences.append(sentence.strip())
            check.removed_sentences.append(sentence.strip())
            continue
        statute_citations = extract_citations(sentence)
        document_citations = extract_document_citations(sentence)
        malformed = _malformed_statute_citations(sentence, statute_citations)
        if malformed:
            # A citation-shaped label that is not in the exact contract
            # form cannot be validated against the evidence: treat it as
            # an invalid citation (A4-016 — never trust model output).
            check.invalid_citations.extend(malformed)
            check.removed_sentences.append(sentence.strip())
            continue
        if not statute_citations and not document_citations:
            gated = _prose_gate(sentence, index, check)
            if gated is None:
                continue
            bare = _strip_labels(gated, [])
            has_section_ref = bool(
                PROSE_CITATION_RE.search(bare) or _INDIC_PROSE_RE.search(_ascii_digits(bare))
            )
            if (
                _LEGAL_CLAIM_RE.search(bare)
                and not has_section_ref
                # Punishment/consequence vocabulary with no citation and no
                # evidenced section reference: an uncited legal claim —
                # unless the citation on the immediately following sentence
                # of the same paragraph grounds this claim in the same
                # evidence (sibling-citation grounding).
                and not _sibling_citation_grounds(sentences, position, _content_tokens(bare), index)
            ):
                check.uncited_legal_claims.append(bare)
                check.removed_sentences.append(sentence.strip())
                continue
            kept.append(gated)
            continue

        invalid = [c for c in statute_citations if not index.statute_supported(c)]
        invalid_doc = [d for d in document_citations if not index.document_supported(d)]
        if invalid or invalid_doc:
            check.invalid_citations.extend(invalid)
            check.invalid_document_citations.extend(d.label for d in invalid_doc)
            check.subsection_mismatches.extend(
                c.label
                for c in invalid
                if (c.act_short, c.section_number) in index.keys and c.subsections
            )
            check.removed_sentences.append(sentence.strip())
            continue

        all_labels = [c.label for c in statute_citations] + [d.label for d in document_citations]
        if _self_referential(sentence):
            # An identity/capability sentence is not a legal claim: any
            # citation attached to it is decorative and is stripped.
            check.irrelevant_citations.extend(all_labels)
            kept.append(_strip_labels(sentence, all_labels))
            continue

        bare = _strip_labels(sentence, all_labels)
        content = _content_tokens(bare)
        indic_sentence = _has_indic_content(bare)
        if not content and not indic_sentence:
            # No substantive tokens left: the citation decorates an empty
            # sentence, so it supports nothing.
            check.irrelevant_citations.extend(all_labels)
            kept.append(bare)
            continue

        relevant_statute = [c for c in statute_citations if index.statute_tokens(c) & content]
        relevant_document = [d for d in document_citations if index.document_tokens(d) & content]
        bridged = False
        if not relevant_statute and not relevant_document and indic_sentence:
            # Multilingual bridge (D-077): token overlap is impossible
            # between an Indic sentence and English evidence. Statute
            # citations pass only when the sentence names the cited
            # section number — a citation-specific lexical bridge. Document
            # citations keep existence + page-range validation; their
            # lexical check is waived (counted) because it cannot be
            # computed across scripts.
            bridged = True
            relevant_statute = [
                c for c in statute_citations if _has_section_number(bare, c.section_number)
            ]
            relevant_document = list(document_citations)
        if not relevant_statute and not relevant_document:
            # The sentence shares no content token with anything it cites:
            # treat the citation as unsupported for this claim.
            check.irrelevant_citations.extend(all_labels)
            check.removed_sentences.append(sentence.strip())
            continue
        if bridged and relevant_document:
            # Document relevance was waived for this cross-script sentence
            # (D-077): existence and page-range validation still applied.
            check.relevance_waived += len(relevant_document)

        keep_labels = {c.label for c in relevant_statute} | {d.label for d in relevant_document}
        drop_labels = [label for label in all_labels if label not in keep_labels]
        if drop_labels:
            check.irrelevant_citations.extend(drop_labels)
            kept.append(_strip_labels(sentence, drop_labels))
        else:
            kept.append(sentence)
        check.valid_citations.extend(relevant_statute)
        for citation in relevant_document:
            if citation.document_id not in check.cited_document_ids:
                check.cited_document_ids.append(citation.document_id)

    sanitized = " ".join(part.rstrip() + " " for part in kept).strip()
    sanitized = re.sub(r"\s{2,}", " ", sanitized)
    # Normalize bare document citations ("[{id} p.1]") to the canonical
    # "[Document <id> p.N]" contract form so every downstream consumer
    # (frontend chips, eval) sees one shape.
    sanitized = _BARE_DOCUMENT_RE.sub(
        lambda m: f"[Document {m.group(1)}{f' p.{m.group(2)}' if m.group(2) else ''}]",
        sanitized,
    )
    # Same for ids braced inside a "Document" label ("[Document {id} p.1]").
    sanitized = _CURLY_DOC_LABEL_RE.sub(
        lambda m: f"[Document {m.group(1)}{f' p.{m.group(2)}' if m.group(2) else ''}]",
        sanitized,
    )
    check.citations_removed = len(check.removed_sentences)
    return sanitized, check


def build_sources(
    evidence: list[ScoredChunk], citations: list[Citation]
) -> list[dict[str, object]]:
    """Source evidence for the UI source drawer (A4-006..A4-009).

    One entry per cited section: verbatim chunk text, page numbers, and the
    citation identity, preserving source/chunk traceability.
    """
    wanted = {(c.act_short, c.section_number) for c in citations}
    sources: list[dict[str, object]] = []
    seen: set[str] = set()
    for scored in evidence:
        chunk = scored.chunk
        if (chunk.act_short, chunk.section_number) not in wanted:
            continue
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        sources.append(
            {
                "citation": f"[{chunk.act_short} s.{chunk.section_number}]",
                "act": chunk.act,
                "act_short": chunk.act_short,
                "section_number": chunk.section_number,
                "section_title": chunk.section_title,
                "text": chunk.text,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "source_uri": chunk.source_uri,
                "chunk_id": chunk.chunk_id,
            }
        )
    return sources
