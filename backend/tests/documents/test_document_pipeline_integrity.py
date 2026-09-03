"""Document pipeline integrity tests (2026-09 document audit, task: PDF
ingestion — multi-page attribution, structure preservation, splitting,
duplicates, reference-scoped retrieval over real ingested documents)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.documents.chunker import MAX_CHUNK_CHARS, chunk_document_pages
from app.documents.ingestion import DocumentWorkspace, _InMemoryDocumentIndex
from app.documents.references import AMBIGUOUS_REASON, NO_SUCH_DOCUMENT_REASON
from app.documents.retrieval import DocumentRetrievalService
from app.documents.service import DocumentService, EagerJobRunner
from app.documents.storage import DocumentFileStorage
from app.documents.store import DocumentStore
from app.ingestion.embeddings import HashingEmbedder
from app.ingestion.models import PageText
from tests.documents.pdf_fixtures import make_pdf

SESSION = "session-aaaaaaaa"


def make_stack(tmp_path: Path) -> tuple[DocumentService, DocumentRetrievalService]:
    store = DocumentStore()
    index = _InMemoryDocumentIndex()
    embedder = HashingEmbedder()
    service = DocumentService(
        store,
        DocumentFileStorage(tmp_path / "storage"),
        DocumentWorkspace(store, index, embedder),
        runner=EagerJobRunner(),
        allowed_types={"application/pdf"},
        max_size_bytes=8 * 1024 * 1024,
    )
    retrieval = DocumentRetrievalService(index, embedder, store=store)
    return service, retrieval


async def upload(service: DocumentService, filename: str, pages: list[str]) -> str:
    document = await service.upload(
        session_id=SESSION,
        filename=filename,
        content_type="application/pdf",
        data=make_pdf(pages),
    )
    assert document.status.value == "ready", document.error_code
    return document.document_id


async def test_multipage_page_attribution(tmp_path: Path) -> None:
    """A page-three fact must cite page three, not page one."""
    service, retrieval = make_stack(tmp_path)
    doc_id = await upload(
        service,
        "agreement.pdf",
        [
            "Page one contains the alpha clause about salary.",
            "Page two contains the beta clause about leave.",
            "Page three contains the gamma clause about arbitration.",
        ],
    )
    evidence = retrieval.retrieve(SESSION, "gamma clause about arbitration")
    assert evidence.hits
    for hit in evidence.hits:
        assert hit.document_id == doc_id
    top = evidence.hits[0]
    assert top.page_start == 3 and top.page_end == 3
    assert "gamma" in top.text.lower()


async def test_structured_content_survives_ingestion(tmp_path: Path) -> None:
    """Headings, list items and table rows remain retrievable text."""
    service, retrieval = make_stack(tmp_path)
    await upload(
        service,
        "terms.pdf",
        [
            "TERMS OF EMPLOYMENT\n"
            "1. Notice period\n"
            "- Employee must give thirty days written notice.\n"
            "- Employer may terminate with one month pay in lieu.\n"
            "2. Salary table\n"
            "Basic pay: Rs 50,000 per month\n"
            "Travel allowance: Rs 5,000 per month\n"
        ],
    )
    evidence = retrieval.retrieve(SESSION, "travel allowance per month")
    assert evidence.hits
    assert any("5,000" in hit.text for hit in evidence.hits)


def test_long_page_splits_within_page() -> None:
    """A page longer than the chunk bound splits; page attribution holds."""
    sentence = "This employment agreement clause is repeated for padding. "
    pages = [PageText(index=4, printed_page=5, lines=[sentence * 60])]
    chunks = chunk_document_pages(
        pages, document_id="d9", session_id=SESSION, source_uri="document:d9"
    )
    assert len(chunks) > 1
    assert all(c.page_start == 5 and c.page_end == 5 for c in chunks)
    assert [c.chunk_id for c in chunks] == [
        f"d9-p0005-{seq:03d}" for seq in range(len(chunks))
    ]
    assert all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)
    # No text silently lost across the split.
    joined = " ".join(c.text for c in chunks)
    assert "repeated for padding" in joined


def test_blank_pages_are_skipped_not_indexed() -> None:
    pages = [
        PageText(index=0, printed_page=1, lines=["Real content on page one."]),
        PageText(index=1, printed_page=2, lines=["", "   "]),
        PageText(index=2, printed_page=3, lines=["Real content on page three."]),
    ]
    chunks = chunk_document_pages(
        pages, document_id="d1", session_id=SESSION, source_uri="document:d1"
    )
    assert [c.chunk_id for c in chunks] == ["d1-p0001-000", "d1-p0003-000"]


async def test_duplicate_upload_keeps_both_documents(tmp_path: Path) -> None:
    service, retrieval = make_stack(tmp_path)
    first = await upload(service, "notice.pdf", ["Tenant must vacate in thirty days."])
    second = await upload(service, "notice.pdf", ["Tenant must vacate in thirty days."])
    assert first != second
    listed = service.list(session_id=SESSION)
    assert [d.document_id for d in listed] == [first, second]
    assert all(d.status.value == "ready" for d in listed)
    evidence = retrieval.retrieve(SESSION, "tenant vacate thirty days")
    assert {h.document_id for h in evidence.hits} == {first, second}


async def test_same_filename_different_content_stays_distinct(tmp_path: Path) -> None:
    """Identity is the document id, never the filename."""
    service, retrieval = make_stack(tmp_path)
    old = await upload(service, "agreement.pdf", ["Old version: salary is Rs 10,000."])
    new = await upload(service, "agreement.pdf", ["New version: salary is Rs 90,000."])
    evidence = retrieval.retrieve(SESSION, "new version salary ninety thousand")
    assert evidence.hits[0].document_id == new
    assert "90,000" in evidence.hits[0].text


async def test_reference_scoped_retrieval_over_real_documents(tmp_path: Path) -> None:
    """Positional/latest/filename/ambiguous references over real ingested docs."""
    service, retrieval = make_stack(tmp_path)
    first = await upload(service, "employment-agreement.pdf", ["Notice period is three months."])
    second = await upload(service, "rental-agreement.pdf", ["Notice period is thirty days."])
    third = await upload(service, "legal-demand-notice.pdf", ["Demand of Rs 5,00,000."])

    def ids(query: str) -> set[str]:
        return {h.document_id for h in retrieval.retrieve(SESSION, query).hits}

    assert ids("notice period in the first document") == {first}
    assert ids("notice period in the second document") == {second}
    assert ids("notice period in the latest document") == {third}
    # Filename mention wins over position words appearing in the query.
    assert ids("notice period in the rental agreement") == {second}
    # No positional words: both agreements rank above the unrelated demand.
    unscoped = retrieval.retrieve(SESSION, "notice period")
    unscoped_ids = {h.document_id for h in unscoped.hits}
    assert {first, second}.issubset(unscoped_ids)
    assert unscoped.hits[0].document_id in {first, second}
    # Ambiguous deictic with several documents: no hits, truthful reason.
    ambiguous = retrieval.retrieve(SESSION, "what does that document say about notice")
    assert ambiguous.hits == []
    assert any(AMBIGUOUS_REASON in r for r in ambiguous.reasons)
    # Out-of-range position: refuse truthfully, no hits.
    missing = retrieval.retrieve(SESSION, "notice period in the fifth document")
    assert missing.hits == []
    assert any(NO_SUCH_DOCUMENT_REASON in r for r in missing.reasons)
    # Hits carry identity for the prompt's source-file line.
    hits = retrieval.retrieve(SESSION, "notice period in the first document").hits
    assert hits[0].filename == "employment-agreement.pdf"
    assert hits[0].position == 1


async def test_multi_document_reference_retrieves_both_sides(tmp_path: Path) -> None:
    """'Compare the first and second documents' grounds in both documents."""
    service, retrieval = make_stack(tmp_path)
    first = await upload(service, "employment-agreement.pdf", ["Employer notice period is three months."])
    second = await upload(service, "rental-agreement.pdf", ["Tenant notice period is thirty days."])
    await upload(service, "legal-demand-notice.pdf", ["Demand of Rs 5,00,000."])
    evidence = retrieval.retrieve(SESSION, "compare notice period first and second documents")
    assert {h.document_id for h in evidence.hits} == {first, second}
