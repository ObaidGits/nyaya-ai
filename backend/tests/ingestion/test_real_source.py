"""Real-source smoke tests against the temporary development-fixture PDF.

The file at ``data/raw/BNS_bare_act_2023.pdf`` is **BNSS** (Bharatiya
Nagarik Suraksha Sanhita), not the required BNS source — DhronAI has been
contacted for the correct PDF. Until it arrives, final BNS corpus
validation is BLOCKED; these tests prove the pipeline works end-to-end on
the layout-compatible Gazette fixture and that the source-validation gate
correctly *rejects* it as BNS.

Skipped automatically when the PDF is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.ingestion.extract import PypdfPageExtractor
from app.ingestion.index_store import JsonlChunkSink
from app.ingestion.models import CorpusSpec
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.validation import SourceValidationError

DEV_PDF = Path(__file__).resolve().parents[3] / "data" / "raw" / "BNS_bare_act_2023.pdf"

pytestmark = pytest.mark.skipif(not DEV_PDF.exists(), reason="dev source PDF not present")


def test_dev_fixture_ingests_under_bnss_spec(tmp_path: Path) -> None:
    pipeline = IngestionPipeline(
        spec=CorpusSpec.bnss_dev_fixture(),
        extractor=PypdfPageExtractor(),
        index=JsonlChunkSink(tmp_path / "dev_chunks.jsonl"),
    )
    result = pipeline.run(DEV_PDF, "2026-08-30T00:00:00Z")
    assert result.source.act_title_detected is not None
    assert "Nagarik Suraksha Sanhita" in result.source.act_title_detected
    assert result.section_count >= 500  # BNSS has 531 sections
    assert result.chunk_count >= result.section_count


def test_dev_fixture_rejected_as_bns(tmp_path: Path) -> None:
    """The wrong-corpus gate: same file must NOT validate as BNS."""
    pipeline = IngestionPipeline(
        spec=CorpusSpec.bns(),
        extractor=PypdfPageExtractor(),
        index=JsonlChunkSink(tmp_path / "never.jsonl"),
    )
    with pytest.raises(SourceValidationError, match="does not match expected corpus"):
        pipeline.run(DEV_PDF, "2026-08-30T00:00:00Z")


def test_dev_fixture_first_section_title_associated(tmp_path: Path) -> None:
    """Marginal-note association works on the real Gazette layout."""
    pipeline = IngestionPipeline(
        spec=CorpusSpec.bnss_dev_fixture(),
        extractor=PypdfPageExtractor(),
        index=JsonlChunkSink(tmp_path / "dev_chunks.jsonl"),
    )
    chunks = pipeline.dry_run_chunks(DEV_PDF, "2026-08-30T00:00:00Z")
    first = next(c for c in chunks if c.section_number == "1")
    assert first.section_title is not None
    assert "Short title" in first.section_title
    assert first.act_short == "BNSS"


def test_dev_fixture_deterministic_output(tmp_path: Path) -> None:
    pipeline = IngestionPipeline(
        spec=CorpusSpec.bnss_dev_fixture(),
        extractor=PypdfPageExtractor(),
        index=JsonlChunkSink(tmp_path / "dev_chunks.jsonl"),
    )
    stamp = "2026-08-30T00:00:00Z"
    first = pipeline.dry_run_chunks(DEV_PDF, stamp)
    second = pipeline.dry_run_chunks(DEV_PDF, stamp)
    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]
