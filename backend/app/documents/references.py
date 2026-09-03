"""Document-reference resolution (2026-09 document RAG task).

Resolves natural-language references to the session's uploaded documents —
"the first document", "the latest PDF", "the other document", a filename
mention — to concrete document ids, deterministically from structured
session state (upload order from the document store), never by guessing
from model text or by hardcoding ids/filenames.

Resolution outcomes:

* ``document_ids=None`` — no specific document referenced: search all.
* ``document_ids=[...]`` — the referenced documents: retrieval must be
  filtered to exactly these.
* ``ambiguous=True`` — the reference genuinely cannot be resolved (e.g.
  "that document" with several uploads and no conversation context): the
  caller must ask for clarification instead of guessing.
* ``unresolved_reason`` — the reference points at something that does not
  exist ("the fifth document" with two uploads): the caller refuses
  truthfully.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORD_RE = re.compile(r"[a-z0-9]+")

#: "first", "second", "3rd" → 0-based position. Words only up to a sane
#: bound — deeper positions are unnatural language ("the eleventh document"
#: is far more likely a misreference, caught by the range check).
_ORDINALS = {
    "first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4,
    "1st": 0, "2nd": 1, "3rd": 2, "4th": 3, "5th": 4,
}
#: Newest upload, regardless of count.
_LATEST = {"latest", "last", "newest", "final"}
#: The upload before the newest.
_PREVIOUS = {"previous", "prior", "earlier"}
#: Complement of the conversation context (or of the co-referenced doc).
_OTHER = {"other", "another"}
#: All documents (balanced retrieval, still document-scoped).
_ALL = {"both", "all", "every", "each"}
#: Deictic singular reference — needs conversation context or a single doc.
_DEICTIC = {"that", "this", "the"}
#: "the notice period" is ordinary subject matter, not a document reference:
#: the weak determiner "the" pairs only with unambiguous container nouns.
#: Stronger deixis ("that agreement") may name domain nouns too.
_PURE_DOCUMENT_NOUNS = {
    "document", "documents", "doc", "docs", "pdf", "pdfs", "file", "files",
    "upload", "uploads",
}
_DOMAIN_DOCUMENT_NOUNS = {"agreement", "contract", "notice", "petition"}

#: Reason markers consumed by the generation refusal mapping.
AMBIGUOUS_REASON = "document reference is ambiguous: no single document could be determined"
NO_SUCH_DOCUMENT_REASON = "document reference does not match any uploaded document"


@dataclass(slots=True)
class DocumentReferenceResolution:
    """Outcome of resolving the references in one query."""

    #: Document ids the query is scoped to; None = all session documents.
    document_ids: list[str] | None = None
    #: The reference cannot be resolved — ask for clarification.
    ambiguous: bool = False
    #: The reference points at nothing that exists — refuse truthfully.
    unresolved_reason: str | None = None
    #: Diagnostic trail (never user-facing verbatim).
    notes: list[str] = field(default_factory=list)


def resolve_document_references(
    query: str,
    documents: list[tuple[str, str]],
    *,
    context_document_ids: list[str] | None = None,
) -> DocumentReferenceResolution:
    """Resolve document references in ``query`` against ``documents``.

    ``documents`` is the upload-ordered list of ``(document_id, filename)``
    pairs of the session's READY documents. ``context_document_ids`` are
    the documents cited in the recent conversation (follow-up resolution:
    "that document" / "the other document").
    """
    tokens = _WORD_RE.findall(query.lower())
    by_id = {document_id: position for position, (document_id, _name) in enumerate(documents)}
    context = [d for d in (context_document_ids or []) if d in by_id]
    notes: list[str] = []

    positions: set[int] = set()
    other_ref = False
    all_ref = False
    deictic_ref = False

    for index, token in enumerate(tokens):
        if token in _ORDINALS:
            positions.add(_ORDINALS[token])
        elif token in _LATEST and documents:
            positions.add(len(documents) - 1)
        elif token in _PREVIOUS and documents:
            positions.add(len(documents) - 2)
        elif token in _OTHER:
            other_ref = True
        elif token in _ALL:
            all_ref = True
        elif token in _DEICTIC:
            nouns = (
                _PURE_DOCUMENT_NOUNS if token == "the" else _PURE_DOCUMENT_NOUNS | _DOMAIN_DOCUMENT_NOUNS
            )
            if any(noun in tokens[index + 1 : index + 4] for noun in nouns):
                # "that/this/the document" — determiner followed shortly by a
                # document noun.
                deictic_ref = True

    filename_ids = _match_filenames(query, documents)
    if filename_ids:
        positions.update(by_id[d] for d in filename_ids)
        notes.append(f"filename mention: {sorted(filename_ids)}")

    if other_ref:
        if context:
            complement = [d for d, _ in documents if d not in set(context)]
            if len(complement) == 1:
                notes.append("other = the one remaining document")
                return DocumentReferenceResolution(document_ids=complement, notes=notes)
            if len(complement) > 1:
                # "the other document" names exactly one other upload; with
                # several remaining it is genuinely ambiguous — clarify.
                return DocumentReferenceResolution(
                    ambiguous=True,
                    notes=notes + [f"other-reference complement has {len(complement)} documents"],
                )
            return DocumentReferenceResolution(
                ambiguous=True, notes=notes + ["other-reference complement is empty"]
            )
        return DocumentReferenceResolution(
            ambiguous=True,
            notes=notes + ["'other document' without conversation context"],
        )

    if all_ref:
        notes.append("all-documents reference")
        return DocumentReferenceResolution(document_ids=None, notes=notes)

    if positions:
        out_of_range = sorted(p for p in positions if p >= len(documents))
        if out_of_range and not any(p < len(documents) for p in positions):
            return DocumentReferenceResolution(
                unresolved_reason=NO_SUCH_DOCUMENT_REASON,
                notes=notes + [f"position(s) out of range: {out_of_range}"],
            )
        resolved = sorted(p for p in positions if p < len(documents))
        notes.append(f"positional reference(s): {resolved}")
        return DocumentReferenceResolution(
            document_ids=[documents[p][0] for p in resolved], notes=notes
        )

    if deictic_ref:
        if len(documents) == 1:
            return DocumentReferenceResolution(document_ids=[documents[0][0]], notes=notes)
        if len(context) == 1:
            notes.append("deictic reference resolved from conversation context")
            return DocumentReferenceResolution(document_ids=context, notes=notes)
        if not documents:
            return DocumentReferenceResolution(
                unresolved_reason=NO_SUCH_DOCUMENT_REASON, notes=notes
            )
        return DocumentReferenceResolution(
            ambiguous=True,
            notes=notes + [f"deictic reference with {len(documents)} documents"],
        )

    return DocumentReferenceResolution(document_ids=None, notes=notes)


def reference_free_query(query: str, documents: list[tuple[str, str]]) -> str:
    """Query with reference-bearing tokens removed, for CONTENT matching.

    "notice period in the first document" must match the referenced
    document's text on "notice period" — the positional/document words
    themselves carry no content signal and only dilute the dense/lexical
    match. Strips ordinal/latest/previous words, pure document nouns,
    deictics before document nouns, and matched filename stems. Domain
    nouns (agreement, notice, contract) stay: they are ordinary subject
    matter.
    """
    tokens = _WORD_RE.findall(query.lower())
    stem_tokens: set[str] = set()
    for _document_id, filename in documents:
        stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename).lower()
        stem_tokens.update(t for t in _WORD_RE.findall(stem) if len(t) >= 3)
    drop: set[int] = set()
    for index, token in enumerate(tokens):
        if (
            token in _ORDINALS
            or token in _LATEST
            or token in _PREVIOUS
            or token in _PURE_DOCUMENT_NOUNS
            or (token in stem_tokens and len(token) >= 4)
        ):
            drop.add(index)
        elif token in _DEICTIC and any(
            noun in tokens[index + 1 : index + 4] for noun in _PURE_DOCUMENT_NOUNS
        ):
            drop.add(index)
    return " ".join(t for i, t in enumerate(tokens) if i not in drop)


def _match_filenames(query: str, documents: list[tuple[str, str]]) -> set[str]:
    """Filename mentions: the file's stem words appear in the query.

    A stem with several words matches when all of them appear (in order,
    allowing fillers); a single-word stem matches only when it is long
    enough to be distinctive. Extensions never matter.
    """
    lowered = query.lower()
    matched: set[str] = set()
    for document_id, filename in documents:
        stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename).lower()
        stem_tokens = [t for t in _WORD_RE.findall(stem) if len(t) >= 3]
        if not stem_tokens:
            continue
        if len(stem_tokens) == 1:
            if len(stem_tokens[0]) >= 6 and stem_tokens[0] in _WORD_RE.findall(lowered):
                matched.add(document_id)
            continue
        # Multi-word stem: every stem token present, in order.
        position = 0
        ok = True
        for token in stem_tokens:
            index = lowered.find(token, position)
            if index == -1:
                ok = False
                break
            position = index + len(token)
        if ok:
            matched.add(document_id)
    return matched


def position_label(position_1based: int, total: int) -> str:
    """Human label for one document's upload position ("second uploaded
    document", "first uploaded document (latest)")."""
    labels = {1: "first", 2: "second", 3: "third"}
    label = labels.get(position_1based, f"{position_1based}th")
    if total > 1 and position_1based == total:
        return f"{label} uploaded document (latest)"
    return f"{label} uploaded document"
