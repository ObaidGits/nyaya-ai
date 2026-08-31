"""Forms API tests (REQUIREMENTS B-033..B-040, D-021..D-024)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from app.core.config import Settings
from app.forms.ocr import OcrFallback
from app.forms.pipeline import FormsExtractor
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.forms.fixtures import FakeOcrEngine, make_forms_pdf

SOURCE_PAGES = [
    ["FORM No. 1", "NOTICE FOR APPEARANCE BY THE POLICE", "(See section 35)", "body"],
    ["FORM No. 2", "SUMMONS TO AN ACCUSED PERSON", "(See section 63)", "body"],
    ["(8)On section 309(2).—continuation of form 2"],
    ["FORM No. 3", "WARRANT OF ARREST", "(See section 72)", "body"],
]


def _make_library(tmp_path: Path) -> Path:
    source = tmp_path / "source.pdf"
    source.write_bytes(make_forms_pdf(SOURCE_PAGES))
    output = tmp_path / "forms"
    FormsExtractor(page_start=1, page_end=4, ocr=OcrFallback(FakeOcrEngine(""))).extract(
        str(source), output
    )
    return output


def _app(forms_dir: Path | None) -> FastAPI:
    settings = Settings(
        _env_file=None,
        forms_output_dir=str(forms_dir) if forms_dir else str(Path("/nonexistent-forms")),
        storage_dir=str(Path("/tmp/nyay-forms-storage")),
    )
    return create_app(settings=settings)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(_app(_make_library(tmp_path))) as test_client:
        yield test_client


def test_forms_list_returns_metadata(client: TestClient) -> None:
    response = client.get("/api/v1/forms")
    assert response.status_code == 200
    forms = response.json()
    assert [f["form_number"] for f in forms] == [1, 2, 3]
    entry = forms[1]
    assert entry["title"] == "SUMMONS TO AN ACCUSED PERSON"
    assert entry["source_page_start"] == 2
    assert entry["source_page_end"] == 3
    assert entry["byte_size"] > 0
    assert entry["output_filename"].startswith("FORM-2_")


def test_forms_search_filters_by_title(client: TestClient) -> None:
    response = client.get("/api/v1/forms/search", params={"q": "warrant"})
    assert response.status_code == 200
    assert [f["form_number"] for f in response.json()] == [3]


def test_forms_search_by_number(client: TestClient) -> None:
    response = client.get("/api/v1/forms/search", params={"q": "2"})
    assert response.status_code == 200
    assert [f["form_number"] for f in response.json()] == [2]


def test_forms_search_requires_query(client: TestClient) -> None:
    response = client.get("/api/v1/forms/search")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_form_metadata_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/forms/2")
    assert response.status_code == 200
    data = response.json()
    assert data["form_number"] == 2
    assert data["title"] == "SUMMONS TO AN ACCUSED PERSON"
    assert data["sha256"]
    assert data["extraction_confidence"] > 0


def test_form_download_returns_pdf(client: TestClient) -> None:
    response = client.get("/api/v1/forms/2/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "FORM-2_" in response.headers["content-disposition"]
    assert response.content[:5] == b"%PDF-"


def test_bulk_download_returns_zip_with_all_forms(client: TestClient) -> None:
    response = client.get("/api/v1/forms/download-all")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert "forms_manifest.json" in names
    assert sum(name.startswith("FORM-") for name in names) == 3
    assert archive.read("forms_manifest.json").startswith(b"{")


def test_missing_form_returns_404(client: TestClient) -> None:
    for path in ("/api/v1/forms/99", "/api/v1/forms/99/download"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "FORM_NOT_FOUND"


def test_non_numeric_form_id_returns_422(client: TestClient) -> None:
    response = client.get("/api/v1/forms/abc/download")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unconfigured_library_returns_503(tmp_path: Path) -> None:
    with TestClient(_app(None)) as client:
        for path in ("/api/v1/forms", "/api/v1/forms/1", "/api/v1/forms/1/download"):
            response = client.get(path)
            assert response.status_code == 503, path
            assert response.json()["error"]["code"] == "FORMS_NOT_CONFIGURED"
        response = client.get("/api/v1/forms/search", params={"q": "x"})
        assert response.status_code == 503


def test_forms_are_isolated_from_user_documents(tmp_path: Path) -> None:
    """Statutory forms library is separate from user uploads (isolation scope)."""
    with TestClient(_app(_make_library(tmp_path))) as client:
        forms = client.get("/api/v1/forms").json()
        assert all(not f["output_filename"].startswith(("doc", "document")) for f in forms)
        # And documents endpoints do not expose forms.
        docs = client.get("/api/v1/documents", headers={"X-Session-Id": "session-aaaaaaaa"})
        assert docs.json() == []
