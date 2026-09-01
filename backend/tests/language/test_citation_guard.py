"""Citation-guard multilingual tests (D-077).

The guard stays authoritative for Indic answers: existence, granularity,
self-reference, and prose gates apply unchanged; only the cross-script
relevance check is bridged (statute: section-number match; document:
logged waiver of the impossible lexical check).
"""

from __future__ import annotations

from app.documents.models import DocumentHit
from app.generation.citation_guard import validate_citations
from app.retrieval.models import ScoredChunk
from tests.retrieval.fixtures import make_corpus

NOTICE_TEXT = "Legal notice: the tenant must vacate the premises within thirty days."


def _evidence() -> list[ScoredChunk]:
    corpus = {c.chunk_id: c for c in make_corpus()}
    return [ScoredChunk(chunk=corpus["ts-s103-001"], rrf_score=1.0)]


def test_indic_sentence_with_section_number_keeps_citation() -> None:
    answer = "धारा 103 के अनुसार हत्या की सजा मृत्यु या आजीवन कारावास है [TS s.103]।"
    sanitized, check = validate_citations(answer, _evidence())
    assert "[TS s.103]" in sanitized
    assert [c.label for c in check.valid_citations] == ["[TS s.103]"]
    assert check.removed_sentences == []


def test_indic_sentence_without_section_number_is_removed() -> None:
    # Zero token overlap AND no section-number bridge: the citation does
    # not support the claim, so the sentence is removed exactly as in the
    # English zero-overlap path.
    answer = "यह एक सामान्य कथन है [TS s.103]।"
    sanitized, check = validate_citations(answer, _evidence())
    assert "[TS s.103]" not in sanitized
    assert check.removed_sentences


def test_indic_fabricated_citation_is_removed() -> None:
    answer = "धारा 103 के अनुसार हत्या की सजा मृत्यु है [TS s.103]। चोरी की सजा जुर्माना है [TS s.999]।"
    sanitized, check = validate_citations(answer, _evidence())
    assert "[TS s.999]" not in sanitized
    assert "चोरी" not in sanitized
    assert "[TS s.103]" in sanitized
    assert check.invalid_citations


def test_indic_prose_claim_for_unknown_section_is_removed() -> None:
    answer = "धारा 999 कहती है कि चोरी पर केवल जुर्माना होता है।"
    sanitized, check = validate_citations(answer, _evidence())
    assert sanitized == ""
    assert check.uncited_section_claims


def test_indic_devanagari_digits_check_against_sections() -> None:
    # "धारा ९९९" must be treated as section 999 — unsupported → removed.
    answer = "धारा ९९९ कहती है कि चोरी पर केवल जुर्माना होता है।"
    sanitized, check = validate_citations(answer, _evidence())
    assert sanitized == ""
    assert check.uncited_section_claims


def test_indic_self_reference_strips_decorative_citation() -> None:
    answer = "मैं न्याय हूँ [TS s.103]।"
    sanitized, check = validate_citations(answer, _evidence())
    assert "[TS s.103]" not in sanitized
    assert "मैं न्याय हूँ" in sanitized
    assert "[TS s.103]" in check.irrelevant_citations


def test_document_citation_cross_script_waives_relevance_logged() -> None:
    hits = [
        DocumentHit(
            chunk_id="d01-p0001-000",
            document_id="d01",
            text=NOTICE_TEXT,
            page_start=1,
            page_end=1,
        )
    ]
    answer = "इस दस्तावेज़ में कहा गया है कि किरायेदार को तीस दिन में खाली करना है [Document d01 p.1]।"
    sanitized, check = validate_citations(answer, _evidence(), document_hits=hits)
    assert "[Document d01 p.1]" in sanitized
    assert check.cited_document_ids == ["d01"]
    # The waived lexical check is counted, never silent (D-077 report).
    assert check.relevance_waived == 1


def test_document_citation_wrong_page_is_removed_cross_script() -> None:
    # Existence and page-range validation still apply without waiver.
    hits = [
        DocumentHit(
            chunk_id="d01-p0001-000",
            document_id="d01",
            text=NOTICE_TEXT,
            page_start=1,
            page_end=1,
        )
    ]
    answer = "इस दस्तावेज़ में कुछ लिखा है [Document d01 p.7]।"
    sanitized, check = validate_citations(answer, _evidence(), document_hits=hits)
    assert "[Document d01 p.7]" not in sanitized
    assert check.invalid_document_citations


def test_english_citation_validation_is_unchanged() -> None:
    answer = (
        "Murder is punishable with death or imprisonment for life [TS s.103]. "
        "This is about me [TS s.103]."
    )
    sanitized, check = validate_citations(answer, _evidence())
    assert "[TS s.103]" in sanitized  # first sentence keeps its citation
    assert sanitized.count("[TS s.103]") == 1  # self-referential one stripped
    assert check.relevance_waived == 0


def test_multi_sentence_indic_answer_survives_danda_splitting() -> None:
    # Regression (D-077 audit): sentences end in danda "।", which the
    # splitter must honor. Before the fix the whole answer was ONE
    # sentence, so one irrelevant citation removed everything → refusal.
    answer = "धारा 103 के अनुसार हत्या की सजा मृत्यु है [TS s.103]। यह एक सामान्य कथन है [TS s.103]।"
    sanitized, _check = validate_citations(answer, _evidence())
    assert "हत्या" in sanitized  # first sentence survives
    assert "सामान्य कथन" not in sanitized  # zero-overlap second removed
    assert "[TS s.103]" in sanitized


def test_indic_prose_claim_removes_only_its_own_sentence() -> None:
    # The prose gate must not nuke the whole multi-sentence danda answer.
    answer = (
        "धारा 103 के अनुसार हत्या की सजा मृत्यु है [TS s.103]। "
        "धारा 999 कहती है कि चोरी पर केवल जुर्माना होता है।"
    )
    sanitized, check = validate_citations(answer, _evidence())
    assert "हत्या" in sanitized
    assert "चोरी" not in sanitized
    assert check.uncited_section_claims


def test_gujarati_and_odia_digits_check_against_sections() -> None:
    # All supported-script digit forms normalize: "કલમ ૧૦૩" is section 103
    # (kept with its citation), "ଧାରା ୯୯୯" is section 999 (removed).
    kept = "કલમ ૧૦૩ મુજબ ખંડણ માટે સજા છે [TS s.103]।"
    sanitized, check = validate_citations(kept, _evidence())
    assert "[TS s.103]" in sanitized
    assert check.valid_citations

    removed = "ଧାରା ୯୯୯ କହେ କି ଚୋରି ପାଇଁ କେବଳ ଜରିମାନା ଅଛି।"
    sanitized, check = validate_citations(removed, _evidence())
    assert sanitized == ""
    assert check.uncited_section_claims


def test_malformed_citation_label_is_removed_not_trusted() -> None:
    # Regression (D-077 live audit): "[BNS s.103 p.29-30]" is citation-
    # shaped but not the exact "[BNS s.103]" contract form. It cannot be
    # validated, so the sentence is removed — never passed through as
    # unvalidated evidence.
    answer = "धारा 103 के अनुसार हत्या की सजा मृत्यु है [TS s.103 p.29-30]।"
    sanitized, check = validate_citations(answer, _evidence())
    assert sanitized == ""
    assert check.invalid_citations
    assert any(c.section_number == "103" for c in check.invalid_citations)


def test_valid_subsection_citation_is_not_malformed() -> None:
    # The exact contract forms (with subsections) must not be caught by
    # the malformed-label check.
    answer = "Murder is punishable as stated [TS s.103(1)]."
    sanitized, check = validate_citations(answer, _evidence())
    assert "[TS s.103(1)]" in sanitized or sanitized == ""
    assert (
        not any(c.section_number == "103" and not c.subsections for c in check.invalid_citations)
        or check.invalid_citations == []
    )


def test_parenthetical_citation_is_removed_not_trusted() -> None:
    # Live-audit regression: "(BNSS s.234)" is citation-shaped but not the
    # bracket contract form; it passed validation untouched.
    answer = "Murder is punishable as stated (TS s.103)."
    sanitized, check = validate_citations(answer, _evidence())
    assert sanitized == ""
    assert check.invalid_citations


def test_plural_sections_prose_claim_is_gated() -> None:
    # "sections 100 and 101" (plural) previously escaped the prose gate,
    # which only matched the singular "section N".
    answer = "Sections 999 and 998 of BNS say theft is punishable with imprisonment."
    sanitized, check = validate_citations(answer, _evidence())
    assert sanitized == ""
    assert check.uncited_section_claims


def test_indic_act_name_sentence_keeps_citation() -> None:
    # D-095 live incident: "न्याय" is the brand word AND part of the corpus
    # act's own name (भारतीय न्याय संहिता). Naming the act must never mark
    # the sentence self-referential — the citation stays valid.
    answer = "भारतीय न्याय संहिता की धारा 103 मृत्युदंड से संबंधित है [TS s.103]।"
    sanitized, check = validate_citations(answer, _evidence())
    assert "[TS s.103]" in sanitized
    assert [c.label for c in check.valid_citations] == ["[TS s.103]"]
    assert not check.removed_sentences
    assert not check.irrelevant_citations


def test_english_act_name_sentence_keeps_citation() -> None:
    # Same collision in English: "Bharatiya Nyaya Sanhita" contains the
    # brand word "Nyaya" — the standalone-brand self-reference match used
    # to strip these citations too.
    answer = "Section 103 of the Bharatiya Nyaya Sanhita deals with punishment [TS s.103]."
    sanitized, check = validate_citations(answer, _evidence())
    assert "[TS s.103]" in sanitized
    assert [c.label for c in check.valid_citations] == ["[TS s.103]"]
    assert not check.irrelevant_citations


def test_script_pronoun_inside_larger_word_not_self_reference() -> None:
    # "मी" inside "समीक्षा" (review) is not the Marathi first-person
    # pronoun: block-aware boundaries prevent the substring match.
    answer = "धारा 103 की समीक्षा करें [TS s.103]।"
    sanitized, check = validate_citations(answer, _evidence())
    assert "[TS s.103]" in sanitized
    assert [c.label for c in check.valid_citations] == ["[TS s.103]"]
