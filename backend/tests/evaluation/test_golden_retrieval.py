"""Golden-set retrieval assertions in CI (REQUIREMENTS.md T-011).

Two layers:

1. A corpus-free assertion: golden lookup questions must hit the
   deterministic section-lookup path against a synthetic corpus built from
   the golden expectations themselves. Runs everywhere, including CI.
2. When the development corpus artifact exists (local/dev machines; the
   artifact is gitignored, so CI skips it), the full evaluation runs and
   hybrid must not lose to dense-only on Recall@5.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from app.evaluation.golden import load_golden_set
from app.ingestion.models import Chunk
from app.retrieval.dense import CosineDenseIndex
from app.retrieval.service import RetrievalService
from app.retrieval.sparse import Bm25SparseIndex
from app.retrieval.store import ChunkStore

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "eval" / "golden_set.jsonl"
DEV_CORPUS = REPO_ROOT / "data" / "processed" / "bnss-dev_chunks.jsonl"


def _synthetic_chunk(section: str) -> Chunk:
    return Chunk(
        chunk_id=f"golden-s{section}",
        act="Bharatiya Nagarik Suraksha Sanhita, 2023",
        act_short="BNSS",
        chapter="I",
        chapter_title="PRELIMINARY",
        section_number=section,
        section_title=f"Section {section}",
        subsection=None,
        clause=None,
        text=f"Section {section} of the Sanhita prescribes the procedure described in this chunk.",
        has_illustration=False,
        has_proviso=False,
        has_exception=False,
        page_start=1,
        page_end=1,
        source_uri="pdf:sha256-golden#page=1",
        ingested_at="2026-08-30T00:00:00Z",
    )


def test_golden_lookup_questions_resolve_via_direct_lookup() -> None:
    """Every golden lookup case retrieves its expected section deterministically."""
    cases = load_golden_set(GOLDEN)
    lookup_cases = [case for case in cases if case.type == "lookup" and not case.must_refuse]
    assert lookup_cases, "golden set must contain lookup cases"

    sections = sorted({s for case in lookup_cases for s in case.expected_sections})
    store = ChunkStore([_synthetic_chunk(section) for section in sections])
    service = RetrievalService(
        store=store,
        dense=CosineDenseIndex(store.chunks, _HashingStub()),
        sparse=Bm25SparseIndex(store.chunks),
    )
    hits = 0
    for case in lookup_cases:
        evidence = service.retrieve(case.question)
        retrieved_sections = {result.chunk.section_number for result in evidence.results}
        if retrieved_sections & set(case.expected_sections):
            hits += 1
    assert hits == len(lookup_cases), (
        f"only {hits}/{len(lookup_cases)} golden lookup questions retrieved their expected section"
    )


class _HashingStub:
    """Deterministic embedder matching the app's hashing seam."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        from app.ingestion.embeddings import HashingEmbedder

        return HashingEmbedder().embed_texts(texts)


@pytest.mark.skipif(not DEV_CORPUS.exists(), reason="development corpus artifact not present")
def test_hybrid_beats_or_matches_dense_on_golden_recall() -> None:
    """Full evaluation over the dev corpus: hybrid Recall@5 >= dense-only."""
    from app.evaluation.runner import run_evaluation

    results = asyncio.run(run_evaluation(GOLDEN, DEV_CORPUS))
    by_name = {config["config"]: config for config in results["configurations"]}
    assert by_name["hybrid"]["recall_at_5"] >= by_name["dense_only"]["recall_at_5"]


def test_golden_set_shape_is_ci_safe() -> None:
    """The shipped golden file loads and mixes refusal + lookup + reasoning."""
    cases = load_golden_set(GOLDEN)
    types = {case.type for case in cases}
    assert "lookup" in types
    assert "reasoning" in types
    assert sum(1 for case in cases if case.must_refuse) >= 5
    # Sanity: the file parses as strict JSONL (CI executes against this file).
    for line in GOLDEN.read_text().splitlines():
        if line.strip():
            json.loads(line)
