"""Document-reference resolution tests (2026-09 document RAG task)."""

from __future__ import annotations

from app.documents.references import (
    AMBIGUOUS_REASON,
    NO_SUCH_DOCUMENT_REASON,
    resolve_document_references,
)

DOCS = [
    ("d1" * 16, "employment-agreement.pdf"),
    ("d2" * 16, "rental-agreement.pdf"),
    ("d3" * 16, "legal-demand-notice.pdf"),
]
D1, D2, D3 = (d[0] for d in DOCS)


def test_ordinal_selectors() -> None:
    assert resolve_document_references("notice period in the first document", DOCS).document_ids == [D1]
    assert resolve_document_references("the second document notice period", DOCS).document_ids == [D2]
    assert resolve_document_references("3rd document", DOCS).document_ids == [D3]


def test_latest_and_previous() -> None:
    assert resolve_document_references("summarize the latest document", DOCS).document_ids == [D3]
    assert resolve_document_references("the last uploaded pdf", DOCS).document_ids == [D3]
    assert resolve_document_references("the previous document", DOCS).document_ids == [D2]


def test_filename_mention() -> None:
    r = resolve_document_references("summarize the rental agreement", DOCS)
    assert r.document_ids == [D2]
    r = resolve_document_references("what does the employment agreement say", DOCS)
    assert r.document_ids == [D1]


def test_multiple_references_compare() -> None:
    r = resolve_document_references("compare the first and second documents", DOCS)
    assert r.document_ids == [D1, D2]


def test_all_documents_reference() -> None:
    assert resolve_document_references("notice periods in all my documents", DOCS).document_ids is None


def test_no_reference_searches_all() -> None:
    assert resolve_document_references("what is the notice period", DOCS).document_ids is None


def test_domain_noun_with_weak_determiner_is_not_deictic() -> None:
    """'the notice period' is subject matter, not a document reference."""
    r = resolve_document_references("what is the notice period in my contract", DOCS)
    assert r.document_ids is None and not r.ambiguous


def test_domain_noun_with_strong_deictic_is_ambiguous() -> None:
    """'that agreement' with several uploads needs clarification."""
    r = resolve_document_references("summarize that agreement", DOCS)
    assert r.ambiguous


def test_out_of_range_reference() -> None:
    r = resolve_document_references("the fifth document", DOCS)
    assert r.document_ids is None
    assert r.unresolved_reason == NO_SUCH_DOCUMENT_REASON


def test_deictic_with_single_document() -> None:
    r = resolve_document_references("what does that document say", [DOCS[0]])
    assert r.document_ids == [D1]


def test_deictic_ambiguous_with_multiple_documents() -> None:
    r = resolve_document_references("what does that document say", DOCS)
    assert r.ambiguous


def test_deictic_resolved_from_conversation_context() -> None:
    r = resolve_document_references(
        "read that document again", DOCS, context_document_ids=[D2]
    )
    assert r.document_ids == [D2]


def test_other_document_single_complement_from_context() -> None:
    docs = [DOCS[0], DOCS[1]]
    r = resolve_document_references(
        "what about the other document", docs, context_document_ids=[D1]
    )
    assert r.document_ids == [D2]


def test_other_document_multiple_complements_is_ambiguous() -> None:
    """'the other document' with two remaining uploads needs clarification."""
    r = resolve_document_references(
        "what about the other document", DOCS, context_document_ids=[D1]
    )
    assert r.ambiguous


def test_other_document_without_context_is_ambiguous() -> None:
    assert resolve_document_references("what about the other document", DOCS).ambiguous


def test_ambiguity_marker_string() -> None:
    assert "ambiguous" in AMBIGUOUS_REASON
    assert "uploaded" in NO_SUCH_DOCUMENT_REASON


def test_position_reference_ignores_statute_sections() -> None:
    """A section number is not a document position."""
    r = resolve_document_references("what does section 103 say", DOCS)
    assert r.document_ids is None and not r.ambiguous
