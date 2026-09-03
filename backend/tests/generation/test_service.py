"""Grounded generation service tests (REQUIREMENTS A4-*; ARCHITECTURE §17, §15)."""

from __future__ import annotations

import pytest
from app.generation.prompt import SYSTEM_PROMPT, build_generation_request
from app.generation.service import REFUSAL_RESPONSE, GenerationService
from app.retrieval.models import ScoredChunk
from tests.generation.fixtures import (
    GOOD_ANSWER,
    MIXED_ANSWER,
    FailingProvider,
    ScriptedProvider,
    history,
    make_evidence,
)


def test_grounding_prompt_contains_evidence_and_rules() -> None:
    evidence = make_evidence()
    request = build_generation_request("What is the punishment for murder?", evidence.results)
    user_message = request.messages[-1]
    text = user_message.content
    for scored in evidence.results:
        assert scored.chunk.text in text
        assert f"[{scored.chunk.act_short} s.{scored.chunk.section_number}]" in text
    assert "What is the punishment for murder?" in text
    assert "ONLY" in SYSTEM_PROMPT
    assert REFUSAL_RESPONSE in SYSTEM_PROMPT


def test_system_prompt_has_no_unreplaced_placeholders() -> None:
    # The citation-format rule used literal {section}/{subsection} tokens that
    # were never substituted — models saw raw braces and could echo them into
    # answers (failing citation validation). Format placeholders addressed TO
    # the model must be descriptive (<section number>), not {curly}.
    evidence = make_evidence()
    request = build_generation_request("What is the punishment for murder?", evidence.results)
    system = request.messages[0].content
    assert "{section}" not in system
    assert "{subsection}" not in system
    assert "{act_short}" not in system
    assert "<section number>" in system  # the format rule stays intact


async def test_grounded_answer_with_valid_citations() -> None:
    provider = ScriptedProvider([GOOD_ANSWER])
    outcome = await GenerationService(provider).answer(
        "What is the punishment for murder?", make_evidence()
    )
    assert not outcome.refused
    assert outcome.answer == GOOD_ANSWER
    assert len(outcome.citations.valid_citations) == 2
    assert len(outcome.sources) == 2
    assert outcome.model == "scripted"


async def test_invalid_citation_sentences_are_stripped_after_retry() -> None:
    # First answer has a fabricated citation; the retry repeats the offence,
    # so the final answer is the sanitized first-pass text.
    provider = ScriptedProvider([MIXED_ANSWER, MIXED_ANSWER, MIXED_ANSWER])
    outcome = await GenerationService(provider).answer(
        "What is theft?", make_evidence(query="what is theft?")
    )
    assert "s.999" not in outcome.answer
    assert "[TS s.103]." in outcome.answer
    assert outcome.citations.invalid_citations
    assert len(provider.requests) == 3  # two regeneration attempts


async def test_generation_only_receives_retrieved_evidence() -> None:
    provider = ScriptedProvider([GOOD_ANSWER])
    evidence = make_evidence()
    await GenerationService(provider).answer("question", evidence)
    request = provider.requests[0]
    prompt_text = " ".join(m.content for m in request.messages)
    # The full corpus is never supplied — only the retrieved chunks.
    assert "Rash driving" not in prompt_text
    assert evidence.results[0].chunk.text in prompt_text


async def test_insufficient_evidence_refuses_without_calling_model() -> None:
    provider = ScriptedProvider([GOOD_ANSWER])
    outcome = await GenerationService(provider).answer(
        "What does section 999 say?", make_evidence(sufficient=False, confidence=0.0, chunks=[])
    )
    assert outcome.refused
    assert outcome.answer == REFUSAL_RESPONSE
    assert not provider.requests  # gate runs before any LLM call (§15)


async def test_low_confidence_evidence_refuses() -> None:
    provider = ScriptedProvider([GOOD_ANSWER])
    outcome = await GenerationService(provider).answer(
        "vague question", make_evidence(sufficient=False, confidence=0.02)
    )
    assert outcome.refused
    assert outcome.answer == REFUSAL_RESPONSE


async def test_provider_failure_surfaces_app_error() -> None:
    from app.core.errors import AppError

    with pytest.raises(AppError):
        await GenerationService(FailingProvider()).answer("question", make_evidence())


async def test_multi_turn_history_is_passed_to_provider() -> None:
    provider = ScriptedProvider([GOOD_ANSWER])
    turns = history(("user", "What is murder?"), ("assistant", "It is an offence."))
    await GenerationService(provider).answer(
        "And its punishment?", make_evidence(query="and its punishment?"), turns
    )
    sent = [m.content for m in provider.requests[0].messages]
    assert "What is murder?" in sent
    assert "It is an offence." in sent


async def test_empty_provider_responses_raise_app_error() -> None:
    """Empty completions are provider failures, never blank answers."""
    from app.generation.service import EmptyGenerationError

    with pytest.raises(EmptyGenerationError):
        await GenerationService(ScriptedProvider(["", ""])).answer("question", make_evidence())


async def test_model_emitted_refusal_text_is_normalized() -> None:
    """When the model itself outputs the refusal string, the outcome is a
    truthful code-level refusal (refused=True), not a grounded answer."""
    provider = ScriptedProvider([REFUSAL_RESPONSE])
    outcome = await GenerationService(provider).answer("question", make_evidence())
    assert outcome.refused
    assert outcome.answer.startswith(REFUSAL_RESPONSE)


async def test_answer_with_no_grounded_sentence_refuses() -> None:
    """Every sentence stripped by the guard → the specification refusal."""
    provider = ScriptedProvider(["Theft is punishable with imprisonment [TS s.999]."] * 2)
    outcome = await GenerationService(provider).answer("question", make_evidence())
    assert outcome.refused
    assert outcome.answer.startswith(REFUSAL_RESPONSE)


async def test_document_sources_only_for_cited_documents() -> None:
    """Source drawer entries are emitted only for documents the answer
    actually cites (A5-012) — citing one document must not leak the other."""
    from app.documents.models import DocumentHit

    cited = DocumentHit(
        chunk_id="d-a-000",
        document_id="aaaa",
        text="The tenant must vacate the premises.",
        page_start=1,
        page_end=1,
        score=0.9,
    )
    other = DocumentHit(
        chunk_id="d-b-000",
        document_id="bbbb",
        text="Unrelated arbitration clause text.",
        page_start=2,
        page_end=2,
        score=0.8,
    )
    evidence = make_evidence()
    evidence.document_hits = [cited, other]
    provider = ScriptedProvider(["The notice says the tenant must vacate [Document aaaa p.1]."])
    outcome = await GenerationService(provider).answer("What does my notice say?", evidence)
    doc_sources = [s for s in outcome.sources if s["source_type"] == "user_document"]
    assert len(doc_sources) == 1
    assert doc_sources[0]["document_id"] == "aaaa"
    assert "arbitration" not in str(outcome.sources)


async def test_irrelevant_citation_creates_no_source() -> None:
    """A citation whose sentence shares no content with the chunk is
    stripped and must not mint a source-drawer entry."""
    provider = ScriptedProvider(["The sky is deep cobalt blue [TS s.103]."] * 2)
    outcome = await GenerationService(provider).answer("question", make_evidence())
    assert outcome.sources == []


async def test_hedged_refusal_text_is_normalized() -> None:
    """A model that prefixes the refusal ("...therefore: I don't know
    based on the available source material.") is still a refusal —
    observed live with qwen2.5:3b (D-077 audit)."""
    from app.generation.service import GenerationService
    from tests.generation.fixtures import ScriptedProvider, make_evidence

    provider = ScriptedProvider(
        [
            "The evidence does not contain the answer. Therefore: "
            "I don't know based on the available source material."
        ]
    )
    outcome = await GenerationService(provider).answer("q?", make_evidence())
    assert outcome.refused is True
    assert outcome.answer.startswith("I don't know based on the available source material.")


async def test_answer_without_any_citation_refuses() -> None:
    """QA red-team regression: a model that obeys "don't cite the answer"
    produces grounded-looking prose with ZERO citations. Every legal
    answer must carry at least one citation — refuse instead."""
    provider = ScriptedProvider(["Theft is punishable with imprisonment for a term of years."] * 2)
    outcome = await GenerationService(provider).answer("question", make_evidence())
    assert outcome.refused
    assert outcome.answer.startswith(REFUSAL_RESPONSE)


def _provider_down_error() -> Exception:
    from app.core.errors import AppError

    class _ProviderDown(AppError):
        status_code = 503
        code = "LLM_PROVIDER_UNAVAILABLE"

    return _ProviderDown("The generation provider is currently unavailable.")


async def test_regeneration_failure_preserves_valid_answer() -> None:
    """Live repro: attempt 1 produces a valid sanitized answer, the guard
    triggers a regeneration, and the regeneration call raises (provider
    429/exception). The valid answer must be returned, not swallowed by the
    propagating error ("chat service unavailable" instead of a good answer)."""
    provider = ScriptedProvider([MIXED_ANSWER, _provider_down_error()])
    outcome = await GenerationService(provider).answer(
        "What is theft?", make_evidence(query="what is theft?")
    )
    assert not outcome.refused
    assert outcome.answer == "Murder is punishable with death [TS s.103]."
    assert len(outcome.citations.valid_citations) == 1
    assert len(provider.requests) == 2  # regeneration was attempted, then failed


async def test_regeneration_failure_without_valid_answer_propagates() -> None:
    """Attempt 1 is entirely ungrounded (every sentence stripped) and the
    regeneration raises: no valid answer exists, so the provider error
    propagates — real failures are never swallowed."""
    from app.core.errors import AppError

    provider = ScriptedProvider(
        ["Theft is punishable with imprisonment [TS s.999].", _provider_down_error()]
    )
    with pytest.raises(AppError):
        await GenerationService(provider).answer("question", make_evidence())


async def test_first_attempt_provider_failure_propagates() -> None:
    """A provider failure before ANY text was produced is a real failure."""
    from app.core.errors import AppError

    provider = ScriptedProvider([_provider_down_error(), GOOD_ANSWER])
    with pytest.raises(AppError):
        await GenerationService(provider).answer("question", make_evidence())


async def test_regeneration_refusal_preserves_valid_answer() -> None:
    """Live repro (1-in-25 intermittent refusal on "What is the punishment
    for murder?"): attempt 1 produces a partially valid answer, the guard
    removes the fabricated sentence and regenerates, and the regeneration
    returns the exact specification refusal string. The preserved valid
    answer must be returned — the user saw a refusal for a question the
    evidence fully grounds."""
    provider = ScriptedProvider([MIXED_ANSWER, REFUSAL_RESPONSE])
    outcome = await GenerationService(provider).answer(
        "What is murder?", make_evidence(query="what is murder?")
    )
    assert not outcome.refused
    assert outcome.answer == "Murder is punishable with death [TS s.103]."
    assert len(outcome.citations.valid_citations) == 1
    assert len(provider.requests) == 2


async def test_first_attempt_refusal_still_refuses() -> None:
    """A refusal on the FIRST attempt (nothing valid preserved) stays a
    refusal: the preservation path must only rescue guarded answers."""
    provider = ScriptedProvider([REFUSAL_RESPONSE])
    outcome = await GenerationService(provider).answer(
        "question", make_evidence(query="theft")
    )
    assert outcome.refused
    assert outcome.answer.startswith(REFUSAL_RESPONSE)


async def test_regeneration_refusal_without_valid_prefix_refuses() -> None:
    """Attempt 1 is entirely ungrounded (guard strips everything), the
    regeneration refuses: the honest outcome is the refusal, never a
    preserved empty answer."""
    provider = ScriptedProvider(
        ["Theft is punishable with imprisonment [TS s.999].", REFUSAL_RESPONSE]
    )
    outcome = await GenerationService(provider).answer("question", make_evidence())
    assert outcome.refused
    assert outcome.answer.startswith(REFUSAL_RESPONSE)


def test_evidence_block_header_includes_subsection() -> None:
    # Regression (2026-09-03): a narrowed chunk (e.g. s.2(11)) carried its
    # subsection in metadata but the evidence header showed the bare section
    # label [TS s.2] — the model could not tie the fragment to the asked
    # subsection and refused. The header must carry the full form.
    from tests.retrieval.fixtures import _chunk

    narrowed = ScoredChunk(
        chunk=_chunk(
            "ts-s002-011",
            "2",
            "Definitions (part 12 of 40)",
            "'good faith'.Nothing is said to be done or believed in 'good "
            "faith' which is done or believed without due care and attention;",
            subsection="(11)",
        ),
        rrf_score=1.0,
    )
    evidence = make_evidence(chunks=[narrowed], query="what is section 2(11)?")
    request = build_generation_request("What is section 2(11)?", evidence.results)
    assert "[TS s.2(11)]" in request.messages[-1].content


def test_evidence_block_header_omits_subsection_when_absent() -> None:
    evidence = make_evidence()
    request = build_generation_request("What is the punishment for murder?", evidence.results)
    text = request.messages[-1].content
    # Chunk 001 carries no subsection → bare label; chunk 002 carries
    # subsection (1) → full form (2026-09-03 subsection headers).
    assert "[TS s.103]" in text
    assert "[TS s.103(1)]" in text
    assert "s.103(" not in text.replace("[TS s.103(1)]", "")


async def test_model_refusal_carries_contextual_reason() -> None:
    # Regression (2026-09-03): a model-emitted refusal (retrieval succeeded
    # but the grounded model found no answer — e.g. bail, which lives in the
    # BNSS, not the BNS corpus) returned the bare refusal line with no
    # user-facing reason. It now carries the code-authored reason sentence.
    provider = ScriptedProvider([REFUSAL_RESPONSE])
    outcome = await GenerationService(provider).answer(
        "What is bail under BNS?", make_evidence(query="what is bail under bns?")
    )
    assert outcome.refused
    assert outcome.refusal_reason is not None
    assert outcome.refusal_reason in outcome.answer
    assert "different statute" in outcome.refusal_reason
