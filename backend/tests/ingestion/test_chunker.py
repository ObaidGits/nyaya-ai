"""Chunker tests (REQUIREMENTS A1-001, A1-002..A1-029, T-002, T-003)."""

from app.ingestion.chunker import StructureAwareChunker
from app.ingestion.models import Block, BlockKind, ParsedAct, Section
from app.ingestion.parser import StructureParser
from tests.ingestion.fixtures import FIXTURE_SPEC, make_pages

URI = "pdf:sha256-abc123def456"
TS = "2026-08-30T00:00:00Z"


def _chunk_act(pages, max_chars=4000):
    act = StructureParser(FIXTURE_SPEC).parse(pages)
    return act, StructureAwareChunker(max_chars=max_chars).chunk(act, URI, TS)


def test_short_section_single_chunk() -> None:
    act, chunks = _chunk_act(make_pages())
    s2 = [c for c in chunks if c.section_number == "2"]
    assert len(s2) == 1
    assert s2[0].text == act.sections[1].text


def test_chunk_id_deterministic_and_unique() -> None:
    _, chunks = _chunk_act(make_pages())
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert all(ids[0].startswith("ts-s") for ids in [[c.chunk_id] for c in chunks])
    # Re-chunking the same input yields identical ids/order.
    _, chunks2 = _chunk_act(make_pages())
    assert [c.chunk_id for c in chunks2] == ids


def test_full_metadata_schema_present() -> None:
    _, chunks = _chunk_act(make_pages())
    for chunk in chunks:
        payload = chunk.model_dump()
        for field in (
            "act",
            "act_short",
            "chapter",
            "chapter_title",
            "section_number",
            "section_title",
            "subsection",
            "clause",
            "text",
            "has_illustration",
            "has_proviso",
            "has_exception",
            "page_start",
            "page_end",
            "chunk_id",
            "source_uri",
            "ingested_at",
            "references",
        ):
            assert field in payload


def test_proviso_exception_explanation_illustration_attached_to_parent() -> None:
    _, chunks = _chunk_act(make_pages())
    s3 = [c for c in chunks if c.section_number == "3"]
    assert len(s3) == 1
    chunk = s3[0]
    assert chunk.has_proviso is True
    assert chunk.has_exception is True
    assert "Provided that" in chunk.text
    assert "Exception" in chunk.text
    assert "Explanation" in chunk.text
    assert "Illustration" in chunk.text


def test_long_section_splits_only_at_subsection_boundaries() -> None:
    _, chunks = _chunk_act(make_pages(), max_chars=700)
    s5 = [c for c in chunks if c.section_number == "4"]
    assert len(s5) > 1
    # Every chunk must cover whole subsections, never a partial sentence.
    for chunk in s5:
        assert chunk.text.rstrip().endswith((".", ";", ")"))


def test_no_mid_sentence_split() -> None:
    _, chunks = _chunk_act(make_pages(), max_chars=700)
    for chunk in chunks:
        for line in chunk.text.splitlines():
            assert not line.startswith(("and ", "the ", "of "))


def test_parent_context_preserved_on_split_chunks() -> None:
    act, chunks = _chunk_act(make_pages(), max_chars=700)
    s5 = [c for c in chunks if c.section_number == "4"]
    assert len(s5) > 1
    for chunk in s5:
        assert chunk.act == act.act
        assert chunk.act_short == act.act_short
        assert chunk.chapter == "II"
        assert chunk.chapter_title == "OFFENCES"
        assert chunk.section_number == "4"
        assert chunk.section_title is not None
        assert chunk.source_uri.startswith("pdf:sha256-")
        assert "#page=" in chunk.source_uri


def test_cross_references_stored_in_chunk_metadata() -> None:
    _, chunks = _chunk_act(make_pages())
    s2 = next(c for c in chunks if c.section_number == "2")
    assert "section 4" in s2.references
    assert "section 2(11)" in s2.references
    s5 = next(c for c in chunks if c.section_number == "4")
    assert "section 164(1)" in s5.references
    assert "section 81" in s5.references
    assert "section 84" in s5.references


def test_page_metadata_preserved() -> None:
    _, chunks = _chunk_act(make_pages())
    s1 = next(c for c in chunks if c.section_number == "1")
    assert s1.page_start == 1
    assert s1.page_end == 1
    s5 = next(c for c in chunks if c.section_number == "4")
    assert s5.page_start == 2
    assert s5.page_end == 2


def test_oversized_unit_without_boundary_stays_whole() -> None:
    # A single enormous block with no subsection boundary must not be split.
    act = ParsedAct(
        act="Test Sanhita, 2023",
        act_short="TS",
        act_title_detected="The Test Sanhita, 2023",
        sections=[
            Section(
                number=1,
                title="Big",
                title_confident=True,
                chapter_number="I",
                chapter_title="PRELIMINARY",
                page_start=1,
                page_end=1,
                blocks=[
                    Block(
                        kind=BlockKind.BODY,
                        text="word " * 2000,
                        page=1,
                        subsection=None,
                        clause=None,
                    )
                ],
            )
        ],
    )
    chunker = StructureAwareChunker(max_chars=100)
    chunks = chunker.chunk(act, URI, TS)
    assert len(chunks) == 1
    assert any("no subsection boundary" in w for w in chunker.warnings)


def test_part_suffix_in_title_for_split_sections() -> None:
    _, chunks = _chunk_act(make_pages(), max_chars=700)
    s5 = [c for c in chunks if c.section_number == "4"]
    assert len(s5) > 1
    for i, chunk in enumerate(s5, start=1):
        assert chunk.section_title == f"Long section (part {i} of {len(s5)})"
