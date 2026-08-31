"""Citation guard tests (REQUIREMENTS A4-001..A4-016; ARCHITECTURE §18-§19)."""

from __future__ import annotations

from typing import Any

from app.generation.citation_guard import (
    build_sources,
    extract_citations,
    validate_citations,
)
from app.retrieval.models import ScoredChunk
from tests.generation.fixtures import GOOD_ANSWER, MIXED_ANSWER, UNCITED_ANSWER, make_evidence


def _evidence_chunks() -> list[ScoredChunk]:
    return make_evidence().results


def test_extract_bracket_citations_with_subsections() -> None:
    citations = extract_citations(GOOD_ANSWER)
    assert [(c.act_short, c.section_number, c.subsections) for c in citations] == [
        ("TS", "103", ""),
        ("TS", "103", "(1)"),
    ]
    assert citations[0].label == "[TS s.103]"
    assert citations[1].label == "[TS s.103(1)]"


def test_valid_citations_are_preserved() -> None:
    sanitized, check = validate_citations(GOOD_ANSWER, _evidence_chunks())
    assert sanitized == GOOD_ANSWER
    assert len(check.valid_citations) == 2
    assert not check.invalid_citations
    assert not check.removed_sentences
    assert check.citations_removed == 0


def test_invalid_citation_removes_its_sentence() -> None:
    sanitized, check = validate_citations(MIXED_ANSWER, _evidence_chunks())
    # Valid sentence survives, fabricated-section sentence is removed.
    assert "Murder is punishable with death [TS s.103]." in sanitized
    assert "s.999" not in sanitized
    assert check.citations_removed == 1
    assert check.invalid_citations[0].label == "[TS s.999]"
    assert any("Theft" in s for s in check.removed_sentences)


def test_all_invalid_citations_yield_empty_answer() -> None:
    sanitized, check = validate_citations(
        "Theft is punishable with imprisonment [TS s.999].", _evidence_chunks()
    )
    assert sanitized == ""
    assert check.citations_removed == 1


def test_uncited_prose_section_claim_is_removed() -> None:
    # Unsupported prose section claims are removed with their sentence —
    # stripping only the flag would leave an unsupported legal claim.
    sanitized, check = validate_citations(UNCITED_ANSWER, _evidence_chunks())
    assert sanitized == ""
    assert check.uncited_section_claims  # "Section 999 of BNS" flagged
    assert check.removed_sentences


def test_supported_prose_section_claim_is_kept() -> None:
    answer = "Section 103 provides the punishment for murder."
    sanitized, check = validate_citations(answer, _evidence_chunks())
    assert sanitized == answer
    assert not check.uncited_section_claims
    assert not check.removed_sentences


def test_self_referential_sentence_loses_citation_but_keeps_text() -> None:
    answer = "I am an AI assistant and cannot give legal advice [TS s.103]."
    sanitized, check = validate_citations(answer, _evidence_chunks())
    assert "[TS s.103]" not in sanitized
    assert "cannot give legal advice" in sanitized
    assert check.irrelevant_citations == ["[TS s.103]"]
    assert not check.removed_sentences
    assert not check.valid_citations


def test_content_free_sentence_loses_citation_but_keeps_text() -> None:
    answer = "Section 103 [TS s.103]."
    sanitized, check = validate_citations(answer, _evidence_chunks())
    assert "[TS s.103]" not in sanitized
    assert "Section 103" in sanitized
    assert check.irrelevant_citations == ["[TS s.103]"]


def test_irrelevant_citation_zero_overlap_removes_sentence() -> None:
    # The section exists in the evidence, but the sentence shares no content
    # with it: the citation does not support the claim.
    answer = "The sky is a shade of deep cobalt blue [TS s.103]."
    sanitized, check = validate_citations(answer, _evidence_chunks())
    assert "cobalt" not in sanitized
    assert check.irrelevant_citations == ["[TS s.103]"]
    assert check.removed_sentences


def test_subsection_citation_requires_subsection_evidence() -> None:
    # s.9 exists in evidence but only as a whole-section chunk without the
    # "(7)" marker in its text — the granularity check must reject it.
    from tests.retrieval.fixtures import make_corpus

    corpus = {c.chunk_id: c for c in make_corpus()}
    chunks = [
        ScoredChunk(chunk=corpus["ts-s103-001"], rrf_score=1.0),
        ScoredChunk(chunk=corpus["ts-s9-001"], rrf_score=0.9),
    ]
    answer = "Dangerous driving attracts enhanced penalties [TS s.9(7)]."
    sanitized, check = validate_citations(answer, chunks)
    assert "s.9(7)" not in sanitized
    assert check.subsection_mismatches == ["[TS s.9(7)]"]
    assert check.removed_sentences


def test_document_citation_validated_against_document_hits() -> None:
    from app.documents.models import DocumentHit

    hits = [
        DocumentHit(
            document_id="d31f",
            page_start=1,
            page_end=2,
            text="The tenant must vacate.",
            score=0.9,
            chunk_id="c1",
        )
    ]
    answer = "The notice says the tenant must vacate [Document d31f p.1]."
    sanitized, check = validate_citations(answer, [], document_hits=hits)
    assert sanitized == answer
    assert check.cited_document_ids == ["d31f"]


def test_document_citation_wrong_page_is_removed() -> None:
    from app.documents.models import DocumentHit

    hits = [
        DocumentHit(
            document_id="d31f",
            page_start=1,
            page_end=2,
            text="The tenant must vacate.",
            score=0.9,
            chunk_id="c1",
        )
    ]
    answer = "The notice says the tenant must vacate [Document d31f p.9]."
    sanitized, check = validate_citations(answer, [], document_hits=hits)
    assert "tenant" not in sanitized
    assert check.invalid_document_citations == ["[Document d31f p.9]"]


def test_document_citation_unknown_document_is_removed() -> None:
    answer = "The notice says the tenant must vacate [Document unknown p.1]."
    sanitized, check = validate_citations(answer, [])
    assert "tenant" not in sanitized
    assert check.invalid_document_citations == ["[Document unknown p.1]"]


def test_missing_citations_leave_no_valid_citations() -> None:
    answer = "Murder is punishable with death."
    _, check = validate_citations(answer, _evidence_chunks())
    assert not check.valid_citations
    assert not check.removed_sentences  # nothing fabricated, simply uncited


def test_build_sources_carries_traceability() -> None:
    evidence = _evidence_chunks()
    _, check = validate_citations(GOOD_ANSWER, evidence)
    sources = build_sources(evidence, check.valid_citations)
    assert len(sources) == 2
    for source, scored in zip(sources, evidence, strict=True):
        chunk = scored.chunk
        assert source["citation"] == f"[{chunk.act_short} s.{chunk.section_number}]"
        assert source["text"] == chunk.text  # verbatim chunk
        assert source["page_start"] == chunk.page_start
        assert source["page_end"] == chunk.page_end
        assert source["source_uri"] == chunk.source_uri
        assert source["chunk_id"] == chunk.chunk_id
        assert source["act_short"] == chunk.act_short
        assert source["section_number"] == chunk.section_number
        assert source["section_title"] == chunk.section_title


def test_build_sources_dedupes_by_chunk_id() -> None:
    evidence = _evidence_chunks()
    _, check = validate_citations(GOOD_ANSWER, evidence)
    doubled = [*check.valid_citations, *check.valid_citations]
    sources = build_sources(evidence, doubled)
    assert len(sources) == 2


def test_ipc_misattribution_removed() -> None:
    """A sentence naming an Act absent from the evidence is stripped."""
    answer = (
        "Section 103 of the Indian Penal Code defines murder. "
        "Murder is punishable with death [TS s.103]."
    )
    sanitized, check = validate_citations(answer, _evidence_chunks())
    assert "Indian Penal Code" not in sanitized
    assert len(check.misattributed_act_sentences) == 1
    assert "TS s.103" in sanitized


def test_ipc_misattribution_with_citation_removed() -> None:
    """Even a validly-cited sentence is dropped when it names the wrong Act."""
    answer = "Under the IPC, murder is punishable with death [TS s.103]."
    sanitized, check = validate_citations(answer, _evidence_chunks())
    assert sanitized == ""
    assert check.misattributed_act_sentences


def test_uncited_legal_claim_removed() -> None:
    """Punishment vocabulary with no citation and no section reference is stripped."""
    answer = (
        "The accused shall be punished with imprisonment for life. "
        "Murder is punishable with death [TS s.103]."
    )
    sanitized, check = validate_citations(answer, _evidence_chunks())
    assert "imprisonment for life" not in sanitized
    assert len(check.uncited_legal_claims) == 1
    assert "TS s.103" in sanitized


def test_supported_prose_section_reference_kept() -> None:
    """A legal-claim sentence that references an evidenced section survives."""
    answer = "Section 103 provides the punishment for murder."
    sanitized, check = validate_citations(answer, _evidence_chunks())
    assert "Section 103" in sanitized
    assert not check.uncited_legal_claims


def _hex_hit(document_id: str = "caf5697ce6f9489cbe3c468e8af813b8") -> Any:
    from app.documents.models import DocumentHit

    return DocumentHit(
        document_id=document_id,
        page_start=1,
        page_end=1,
        text="The Widget Agreement requires notice of 30 days.",
        score=0.83,
        chunk_id=f"{document_id}-p0001-000",
    )


def test_bare_hex_document_citation_is_accepted_and_normalized() -> None:
    """Small models drop the literal "Document" word: "[{id} p.1]" still validates."""
    hits = [_hex_hit()]
    answer = (
        "The Widget Agreement requires a notice period of 30 days. "
        "[{caf5697ce6f9489cbe3c468e8af813b8} p.1]"
    )
    sanitized, check = validate_citations(answer, [], document_hits=hits)
    assert check.cited_document_ids == ["caf5697ce6f9489cbe3c468e8af813b8"]
    assert sanitized == (
        "The Widget Agreement requires a notice period of 30 days. "
        "[Document caf5697ce6f9489cbe3c468e8af813b8 p.1]"
    )


def test_label_only_fragment_after_period_merges_into_previous_sentence() -> None:
    """A citation placed after the sentence-final period still counts."""
    hits = [_hex_hit()]
    answer = (
        "The Widget Agreement requires a notice period of 30 days. "
        "[caf5697ce6f9489cbe3c468e8af813b8]"
    )
    sanitized, check = validate_citations(answer, [], document_hits=hits)
    assert check.cited_document_ids == ["caf5697ce6f9489cbe3c468e8af813b8"]
    assert "30 days" in sanitized
    assert "[Document caf5697ce6f9489cbe3c468e8af813b8]" in sanitized


def test_bare_hex_citation_for_unknown_document_is_removed() -> None:
    answer = "The tenant must vacate [abcdef0123456789 p.1]."
    sanitized, check = validate_citations(answer, [])
    assert "tenant" not in sanitized
    assert check.invalid_document_citations == ["[Document abcdef0123456789 p.1]"]


def test_statute_bracket_is_not_misread_as_document_citation() -> None:
    from app.generation.citation_guard import extract_document_citations

    assert extract_document_citations("See [BNS s.103] and [TS s.9(7)].") == []


def test_braced_citation_label_is_normalized() -> None:
    """ "[{Document <id> p.1}]" (stray braces) still validates and is normalized."""
    hits = [_hex_hit("e6bb935e0aae4be49901cb51a70c19e0")]
    answer = (
        "The Widget Agreement requires notice of 30 days. "
        "[{Document e6bb935e0aae4be49901cb51a70c19e0 p.1}]"
    )
    sanitized, check = validate_citations(answer, [], document_hits=hits)
    assert check.cited_document_ids == ["e6bb935e0aae4be49901cb51a70c19e0"]
    assert sanitized == (
        "The Widget Agreement requires notice of 30 days. "
        "[Document e6bb935e0aae4be49901cb51a70c19e0 p.1]"
    )


def test_curly_id_inside_document_label_is_normalized() -> None:
    """ "[Document {id} p.1]" — braces around the id inside the label."""
    hits = [_hex_hit()]
    answer = (
        "The Widget Agreement requires 30 days notice "
        "[Document {caf5697ce6f9489cbe3c468e8af813b8} p.1]."
    )
    sanitized, check = validate_citations(answer, [], document_hits=hits)
    assert check.cited_document_ids == ["caf5697ce6f9489cbe3c468e8af813b8"]
    assert "{caf" not in sanitized
    assert "[Document caf5697ce6f9489cbe3c468e8af813b8 p.1]" in sanitized


def test_citation_after_punctuation_and_adjacent_markdown() -> None:
    """Citations positioned after sentence punctuation or inside markdown survive."""
    answer = (
        "Murder is punishable with death. [TS s.103] "
        "**[TS s.103]** Murder also attracts [TS s.103] and [TS s.103(1)] punishment."
    )
    sanitized, check = validate_citations(answer, _evidence_chunks())
    assert len(check.valid_citations) >= 2
    assert "[TS s.103]" in sanitized  # citations anchored to content survive


def test_altered_document_id_is_rejected() -> None:
    """One hex digit changed = a different (unretrieved) document."""
    hits = [_hex_hit()]
    answer = "Fake claim [Document caf5697ce6f9489cbe3c468e8af813b9 p.1]."
    sanitized, check = validate_citations(answer, [], document_hits=hits)
    assert sanitized == ""
    assert check.invalid_document_citations


def test_invented_page_is_rejected() -> None:
    """A page outside the retrieved hit's range is invalid."""
    hits = [_hex_hit()]
    answer = "Fake page [Document caf5697ce6f9489cbe3c468e8af813b8 p.99]."
    sanitized, check = validate_citations(answer, [], document_hits=hits)
    assert sanitized == ""
    assert check.invalid_document_citations
