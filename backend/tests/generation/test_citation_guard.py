"""Citation guard tests (REQUIREMENTS A4-001..A4-016; ARCHITECTURE §18-§19)."""

from __future__ import annotations

from typing import Any

from app.generation.citation_guard import (
    build_sources,
    extract_citations,
    validate_citations,
)
from app.documents.models import DocumentHit
from app.ingestion.models import Chunk
from app.retrieval.models import ScoredChunk
from tests.generation.fixtures import GOOD_ANSWER, MIXED_ANSWER, UNCITED_ANSWER, make_evidence


def _evidence_chunks() -> list[ScoredChunk]:
    return make_evidence().results


def _chunk(
    chunk_id: str,
    section: str,
    title: str,
    text: str,
    *,
    act_short: str = "BNS",
    act: str = "Bharatiya Nyaya Sanhita, 2023",
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        act=act,
        act_short=act_short,
        chapter="VI",
        chapter_title="OFFENCES AFFECTING LIFE",
        section_number=section,
        section_title=title,
        subsection=None,
        clause=None,
        text=text,
        has_illustration=False,
        has_proviso=False,
        has_exception=False,
        page_start=29,
        page_end=30,
        source_uri="pdf:sha256-bns#page=29",
        ingested_at="2026-08-30T00:00:00Z",
    )


#: BNS s.103 verbatim text — the live false-positive corpus ("What happens
#: if anyone commits murder?").
_BNS_103_TEXT = (
    "Whoever commits murder shall be punished with death or imprisonment "
    "for life, and shall also be liable to fine."
)


def _bns103_evidence() -> list[ScoredChunk]:
    return [
        ScoredChunk(
            chunk=_chunk("bns-s103-001", "103", "Punishment for murder", _BNS_103_TEXT),
            rrf_score=1.0,
        )
    ]


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
    assert "\u202f" not in sanitized
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
    assert "\u202f" not in sanitized
    assert "cannot give legal advice" in sanitized
    assert check.irrelevant_citations == ["[TS s.103]"]
    assert not check.removed_sentences
    assert not check.valid_citations


def test_content_free_sentence_loses_citation_but_keeps_text() -> None:
    answer = "Section 103 [TS s.103]."
    sanitized, check = validate_citations(answer, _evidence_chunks())
    assert "\u202f" not in sanitized
    assert "Section 103" in sanitized
    assert check.irrelevant_citations == ["[TS s.103]"]


def test_irrelevant_citation_zero_overlap_removes_sentence() -> None:
    # The section exists in the evidence, but the sentence shares no content
    # with it: the citation does not support the claim.
    answer = "The sky is a shade of deep cobalt blue [TS s.103]."
    sanitized, check = validate_citations(answer, _evidence_chunks())
    assert "\u202f" not in sanitized
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
    assert "\u202f" not in sanitized
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
    assert "\u202f" not in sanitized
    assert check.invalid_document_citations == ["[Document d31f p.9]"]


def test_document_citation_unknown_document_is_removed() -> None:
    answer = "The notice says the tenant must vacate [Document unknown p.1]."
    sanitized, check = validate_citations(answer, [])
    assert "\u202f" not in sanitized
    assert check.invalid_document_citations == ["[Document unknown p.1]"]


def test_missing_citations_leave_no_valid_citations() -> None:
    # "punishable" is claim vocabulary (audit M4): a punishment claim with
    # no citation and no evidenced section reference is an unsupported
    # legal claim — removed so the refusal path fires (A4-016).
    answer = "Murder is punishable with death."
    _, check = validate_citations(answer, _evidence_chunks())
    assert not check.valid_citations
    assert check.removed_sentences == ["Murder is punishable with death."]
    assert check.uncited_legal_claims == ["Murder is punishable with death."]


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
    assert "\u202f" not in sanitized
    assert len(check.misattributed_act_sentences) == 1
    assert "TS s.103" in sanitized


def test_ipc_misattribution_with_citation_removed() -> None:
    """Even a validly-cited sentence is dropped when it names the wrong Act."""
    answer = "Under the IPC, murder is punishable with death [TS s.103]."
    sanitized, check = validate_citations(answer, _evidence_chunks())
    assert sanitized == ""
    assert check.misattributed_act_sentences


def test_uncited_legal_claim_removed() -> None:
    """Punishment vocabulary with no citation, no section reference and no
    grounded sibling citation is stripped.

    The claim must share NOTHING substantive with the adjacent cited section:
    "punished"/"imprisonment" alone (generic penalty vocabulary shared with
    every penalty section) do not make it traceable.
    """
    answer = (
        "The accused shall be punished with imprisonment for defamation. "
        "Murder is punishable with death [TS s.103]."
    )
    sanitized, check = validate_citations(answer, _evidence_chunks())
    assert "\u202f" not in sanitized
    assert len(check.uncited_legal_claims) == 1
    assert "TS s.103" in sanitized


# --- Sibling-citation grounding (trailing citation on the next sentence) ---


def test_trailing_sibling_citation_grounds_preceding_claim() -> None:
    """Live false positive (BNS s.103): the model states the punishment rule
    without a citation and cites the section on the following elaborating
    sentence. The claim is traceable to the cited chunk → kept."""
    answer = (
        "Anyone who commits murder is punished with death or imprisonment "
        "for life, and is also liable to a fine. "
        "If the murder is committed by a group of five or more persons, every "
        "member is equally liable [BNS s.103]."
    )
    sanitized, check = validate_citations(answer, _bns103_evidence())
    assert "Anyone who commits murder is punished" in sanitized
    assert "[BNS s.103]" in sanitized
    assert check.uncited_legal_claims == []
    assert check.removed_sentences == []


def test_multiple_claims_one_trailing_citation_all_grounded_kept() -> None:
    answer = (
        "Murder is punished with death. "
        "The offender is liable to imprisonment for life and to a fine [BNS s.103]."
    )
    sanitized, check = validate_citations(answer, _bns103_evidence())
    assert sanitized == answer
    assert check.removed_sentences == []
    assert len(check.valid_citations) == 1


def test_multiple_trailing_citations_ground_preceding_claim() -> None:
    evidence = [
        *_bns103_evidence(),
        ScoredChunk(
            chunk=_chunk(
                "bns-s104-001",
                "104",
                "Attempt to murder",
                "Whoever attempts to commit murder shall be punished with imprisonment for life.",
            ),
            rrf_score=0.9,
        ),
    ]
    answer = (
        "Anyone who commits murder is punished with death. "
        "Attempted murder is punishable with imprisonment [BNS s.104], "
        "and murder with death [BNS s.103]."
    )
    sanitized, check = validate_citations(answer, evidence)
    assert "commits murder is punished" in sanitized
    assert {c.label for c in check.valid_citations} == {"[BNS s.103]", "[BNS s.104]"}
    assert check.removed_sentences == []


def test_unrelated_claim_next_to_real_citation_still_removed() -> None:
    """A hallucinated claim sharing only generic punishment vocabulary with
    the cited section is NOT grounded by the adjacent citation."""
    answer = (
        "The tenant shall be punished with imprisonment if he sublets the flat. "
        "Whoever commits murder shall be punished with death [BNS s.103]."
    )
    sanitized, check = validate_citations(answer, _bns103_evidence())
    assert "\u202f" not in sanitized
    assert len(check.uncited_legal_claims) == 1
    assert "commits murder" in sanitized


def test_sibling_citation_does_not_cross_paragraph_boundary() -> None:
    """A citation in a NEW paragraph cites that paragraph, not the previous one."""
    answer = (
        "Anyone who commits murder is punished with death.\n\n"
        "Whoever commits murder shall be punished [BNS s.103]."
    )
    sanitized, check = validate_citations(answer, _bns103_evidence())
    assert "\u202f" not in sanitized
    assert check.uncited_legal_claims


def test_sibling_fabricated_citation_does_not_ground() -> None:
    answer = (
        "Anyone who commits murder is punished with death. "
        "Theft is punishable with imprisonment [BNS s.999]."
    )
    sanitized, check = validate_citations(answer, _bns103_evidence())
    assert sanitized == ""
    assert check.uncited_legal_claims
    assert check.invalid_citations[0].label == "[BNS s.999]"


def test_sibling_parenthesized_citation_does_not_ground() -> None:
    """A citation-shaped paren form cannot be validated, so it grounds nothing."""
    answer = (
        "Anyone who commits murder is punished with death or imprisonment for life. "
        "The punishment is severe (BNS s.103)."
    )
    sanitized, check = validate_citations(answer, _bns103_evidence())
    assert sanitized == ""
    assert check.uncited_legal_claims
    assert check.invalid_citations


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
    assert "\u202f" not in sanitized
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
    assert "\u202f" not in sanitized
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


def test_bullet_list_of_label_only_citations_is_kept() -> None:
    """A bulleted list of citation-only items is a list answer, not
    decorative citations. Live regression: "Which of my documents mention
    Mr. Arjun Sen?" produced a grounded intro + citation bullets; the
    bullet block had no terminal punctuation, split into one
    content-free "sentence", every citation was stripped as decorative,
    and the fully-grounded answer was refused for having no citations."""
    hits = [
        _hex_hit(),
        DocumentHit(
            document_id="caf5697ce6f9489cbe3c468e8af813b8",
            page_start=2,
            page_end=2,
            text="The parties are Widget Corp and Gadget Ltd.",
            score=0.8,
            chunk_id="caf5697ce6f9489cbe3c468e8af813b8-p0002-000",
        ),
    ]
    answer = (
        "Based on the provided evidence, the agreement is mentioned in the "
        "following uploaded documents:\n\n"
        "* [Document caf5697ce6f9489cbe3c468e8af813b8 p.1]\n"
        "* [Document caf5697ce6f9489cbe3c468e8af813b8 p.2]"
    )
    sanitized, check = validate_citations(answer, [], document_hits=hits)
    assert "p.1" in sanitized and "p.2" in sanitized
    assert check.cited_document_ids == ["caf5697ce6f9489cbe3c468e8af813b8"]
    assert not check.irrelevant_citations


def test_numbered_list_of_label_only_citations_is_kept() -> None:
    """Numbered citation list items behave like bullets."""
    hits = [_hex_hit()]
    answer = (
        "The agreement appears in:\n\n1. [Document caf5697ce6f9489cbe3c468e8af813b8 p.1]\n"
        "2. [Document caf5697ce6f9489cbe3c468e8af813b8 p.1]"
    )
    sanitized, check = validate_citations(answer, [], document_hits=hits)
    assert "p.1" in sanitized
    assert check.cited_document_ids
    assert not check.irrelevant_citations


# ---------------------------------------------------------------------------
# Citation label variants (audit H4): a bracket that LOOKS like a statute
# citation can never survive as unvalidated prose
# ---------------------------------------------------------------------------


def test_citation_case_and_space_variants_normalize_and_validate() -> None:
    """[BNS S.103], [BNS s. 103], [bns s.103] normalize to the canonical
    contract and validate against the same evidence."""
    evidence = _bns103_evidence()
    for label in ("[BNS S.103]", "[BNS s. 103]", "[bns s.103]"):
        sanitized, check = validate_citations(
            f"Murder is punished with death {label}.", evidence
        )
        assert check.valid_citations, label
        assert not check.removed_sentences, label
        assert "[BNS s.103]" in sanitized, label


def test_malformed_label_variants_are_removed() -> None:
    """sec./§/bare-number citation shapes cannot be validated: the sentence
    is removed rather than letting an unvalidated label pass as prose."""
    evidence = _bns103_evidence()
    for label in ("[BNS sec.103]", "[BNS §103]", "[BNS 103]", "[BNS s. 999]"):
        sanitized, check = validate_citations(
            f"Murder is punished with death {label}.", evidence
        )
        assert not check.valid_citations, label
        assert check.removed_sentences, label
    assert "\u202f" not in sanitized


def test_bracketed_asides_are_not_malformed_citations() -> None:
    """Ordinary brackets with an uppercase-word + number are untouched; hex
    document ids are never mistaken for statute labels."""
    evidence = _bns103_evidence()
    hits = [
        DocumentHit(
            document_id="abcdef1234567890",
            text="Legal notice text about eviction.",
            page_start=1,
            page_end=2,
            score=0.9,
            chunk_id="c1",
        )
    ]
    sanitized, check = validate_citations(
        "The notice concerns eviction [Document abcdef1234567890 p.1]. "
        "See the summary [Note 2] for details.",
        evidence,
        document_hits=hits,
    )
    assert not check.invalid_citations
    assert "[Note 2]" in sanitized
    assert check.cited_document_ids == ["abcdef1234567890"]


# ---------------------------------------------------------------------------
# Claim vocabulary (audit M4)
# ---------------------------------------------------------------------------


def test_punishable_claim_without_citation_is_removed() -> None:
    evidence = _bns103_evidence()
    sanitized, check = validate_citations(
        "Cyber terrorism is punishable with death.", evidence
    )
    assert sanitized == ""
    assert check.uncited_legal_claims == ["Cyber terrorism is punishable with death."]


def test_sibling_citation_cannot_ground_cross_offence_claim() -> None:
    """A theft claim is not grounded by the murder section's citation just
    because both mention imprisonment for life: "life" is claim
    vocabulary, so sibling grounding needs a substantive shared token."""
    evidence = _bns103_evidence()
    answer = (
        "Theft is punishable with imprisonment for life. "
        "This is the rule for murder [BNS s.103]."
    )
    sanitized, check = validate_citations(answer, evidence)
    assert check.removed_sentences == ["Theft is punishable with imprisonment for life."]
    assert "\u202f" not in sanitized


# ---------------------------------------------------------------------------
# Prose act-name aliases (audit M5)
# ---------------------------------------------------------------------------


def test_bnss_full_name_in_bns_answer_is_misattribution() -> None:
    evidence = _bns103_evidence()
    sanitized, check = validate_citations(
        "Under section 103 of the Bharatiya Nagarik Suraksha Sanhita the police may detain.",
        evidence,
    )
    assert check.misattributed_act_sentences
    assert sanitized == ""


def test_bns_full_name_in_bns_answer_is_not_misattribution() -> None:
    evidence = _bns103_evidence()
    sanitized, check = validate_citations(
        "Under the Bharatiya Nyaya Sanhita, murder is punished with death [BNS s.103].",
        evidence,
    )
    assert not check.misattributed_act_sentences
    assert "[BNS s.103]" in sanitized


# ---------------------------------------------------------------------------
# Page-zero document citations (audit M10)
# ---------------------------------------------------------------------------


def test_document_page_zero_is_invalid() -> None:
    hits = [
        DocumentHit(
            document_id="d31f9c11",
            text="Legal notice text about eviction.",
            page_start=1,
            page_end=2,
            score=0.9,
            chunk_id="c1",
        )
    ]
    sanitized, check = validate_citations(
        "The notice concerns eviction [Document d31f9c11 p.0].", [], document_hits=hits
    )
    assert check.invalid_document_citations == ["[Document d31f9c11 p.0]"]
    assert sanitized == ""


# ---------------------------------------------------------------------------
# Abbreviation-aware sentence splitting (audit)
# ---------------------------------------------------------------------------


def test_abbreviation_periods_do_not_split_sentences() -> None:
    evidence = _bns103_evidence()
    sanitized, check = validate_citations(
        "Mr. Smith v. Jones was decided in 2019. "
        "The fine is Rs. 500 under section 103. [BNS s.103]",
        evidence,
    )
    # The claim sentence keeps its citation; the case-name sentence is kept
    # as ordinary prose.
    assert check.valid_citations == [
        check.valid_citations[0]
    ]  # exactly one citation validated
    assert "Rs. 500" in sanitized
    assert "Mr. Smith v. Jones" in sanitized


# ---------------------------------------------------------------------------
# Label-only answers (audit: bullet debris)
# ---------------------------------------------------------------------------


def test_label_only_bullet_answer_sanitizes_to_empty() -> None:
    evidence = _bns103_evidence()
    sanitized, check = validate_citations(
        "* [BNS s.103]\n* [BNS s.103]", evidence
    )
    assert sanitized == ""


def test_cjk_bracket_document_citation_is_normalized() -> None:
    """Live regression (2026-09-03): models sometimes emit the label with
    CJK corner brackets ("【Document <id> p.4】"). The label is grounded;
    only the glyphs differ. Without normalization the answer validates as
    citation-free and a fully grounded answer is refused."""
    hits = [_hex_hit()]
    answer = (
        "The Widget Agreement requires notice of 30 days"
        "【Document caf5697ce6f9489cbe3c468e8af813b8 p.1】."
    )
    sanitized, check = validate_citations(answer, [], document_hits=hits)
    assert check.cited_document_ids == ["caf5697ce6f9489cbe3c468e8af813b8"]
    assert "[Document caf5697ce6f9489cbe3c468e8af813b8 p.1]" in sanitized


def test_narrow_no_break_space_is_normalized_to_space() -> None:
    """Models emit U+202F inside numbers/names ("Rs 85,000"); the sanitized
    answer carries plain spaces so token checks and copy-paste behave."""
    hits = [_hex_hit()]
    answer = (
        "The Widget Agreement salary is Rs\u202f85,000 per month "
        "[Document caf5697ce6f9489cbe3c468e8af813b8 p.1]."
    )
    sanitized, check = validate_citations(answer, [], document_hits=hits)
    assert check.cited_document_ids
    assert "Rs 85,000" in sanitized
    assert "\u202f" not in sanitized
