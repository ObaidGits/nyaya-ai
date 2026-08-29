"""Source validation + pipeline tests (SRC-002/003/009, replaceable source)."""

from pathlib import Path

import pytest
from app.ingestion.embeddings import NullEmbedder
from app.ingestion.index_store import JsonlChunkSink
from app.ingestion.models import CorpusSpec, PageText
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.validation import SourceValidationError, detect_act_title
from tests.ingestion.fixtures import FIXTURE_SPEC, make_pages


class FakeExtractor:
    """Extractor stub serving canned PageText fixtures."""

    def __init__(self, pages: list[PageText]) -> None:
        self.pages = pages

    def extract(self, source_path: str) -> list[PageText]:
        return self.pages


@pytest.fixture()
def jsonl_sink(tmp_path: Path) -> JsonlChunkSink:
    return JsonlChunkSink(tmp_path / "chunks.jsonl")


def _pipeline(tmp_path: Path, pages, spec=FIXTURE_SPEC, **kwargs) -> IngestionPipeline:
    return IngestionPipeline(
        spec=spec,
        extractor=FakeExtractor(pages),
        index=kwargs.pop("index", JsonlChunkSink(tmp_path / "chunks.jsonl")),
        **kwargs,
    )


def test_detect_act_title_by_content_not_filename() -> None:
    assert detect_act_title(make_pages()) == "The Test Sanhita, 2023"


def test_wrong_act_rejected_by_content(tmp_path: Path) -> None:
    src = tmp_path / "renamed.pdf"  # filename must not matter
    src.write_bytes(b"irrelevant")
    pages = [
        PageText(
            index=0,
            printed_page=1,
            lines=[
                "1",
                "THE  OTHER  SANHITA,  2023",
                "NO. 98 OF 2023",
                "1. Some text.",
            ],
        )
    ]
    with pytest.raises(SourceValidationError, match="does not match expected corpus"):
        _pipeline(tmp_path, pages).run(src, "2026-08-30T00:00:00Z")


def test_mismatched_title_and_spec_rejected(tmp_path: Path) -> None:
    # Extracted content is the fixture act, spec expects something else.
    other = CorpusSpec(
        act="Different Sanhita, 2023",
        act_short="DS",
        title_pattern=r"the\s+different\s+sanhita",
        min_sections=1,
        min_pages=1,
    )
    src = tmp_path / "source.pdf"
    src.write_bytes(b"irrelevant")
    with pytest.raises(SourceValidationError):
        _pipeline(tmp_path, make_pages(), spec=other).run(src, "2026-08-30T00:00:00Z")


def test_too_few_structures_rejected(tmp_path: Path) -> None:
    demanding = CorpusSpec(
        act="Test Sanhita, 2023",
        act_short="TS",
        title_pattern=r"the\s+test\s+sanhita",
        min_sections=50,
        min_pages=1,
    )
    src = tmp_path / "source.pdf"
    src.write_bytes(b"irrelevant")
    with pytest.raises(SourceValidationError, match="structure failed validation"):
        _pipeline(tmp_path, make_pages(), spec=demanding).run(src, "2026-08-30T00:00:00Z")


def test_missing_source_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(SourceValidationError, match="source file not found"):
        _pipeline(tmp_path, make_pages()).run(tmp_path / "missing.pdf", "2026-08-30T00:00:00Z")


def test_successful_run_writes_deterministic_jsonl(tmp_path: Path) -> None:
    src = tmp_path / "source.pdf"
    src.write_bytes(b"irrelevant")
    pipeline = _pipeline(tmp_path, make_pages())
    stamp = "2026-08-30T00:00:00Z"
    result = pipeline.run(src, stamp)
    assert result.validation.ok is True
    assert result.section_count == 4
    assert result.chunk_count > 0

    out = tmp_path / "chunks.jsonl"
    first = out.read_text(encoding="utf-8")
    # Second run over the same source must be byte-identical (no duplicates).
    _pipeline(tmp_path, make_pages()).run(src, stamp)
    assert out.read_text(encoding="utf-8") == first


def test_source_identity_recorded(tmp_path: Path) -> None:
    # sha256 of an actually existing file.
    src = tmp_path / "source.pdf"
    src.write_bytes(b"fake pdf bytes")
    pipeline = _pipeline(tmp_path, make_pages())
    result = pipeline.run(src, "2026-08-30T00:00:00Z")
    assert result.source.filename == "source.pdf"
    assert len(result.source.sha256) == 64
    assert result.source.page_count == 2
    assert result.source.act_title_detected == "The Test Sanhita, 2023"


def test_pipeline_with_embedder_wiring(tmp_path: Path) -> None:
    src = tmp_path / "source.pdf"
    src.write_bytes(b"irrelevant")
    pipeline = _pipeline(tmp_path, make_pages(), embedder=NullEmbedder(dimensions=4))
    result = pipeline.run(src, "2026-08-30T00:00:00Z")
    assert result.chunk_count > 0


def test_fixture_chrome_removed_by_cleaning() -> None:
    # The fixture's raw lines contain Gazette chrome; cleaned pages must not.
    pages = make_pages()
    for page in pages:
        assert not any("GAZETTE" in ln for ln in page.lines)
        assert not any(ln.startswith("xxxGID") for ln in page.lines)
        assert not all(set(ln) == {"_"} for ln in page.lines)
