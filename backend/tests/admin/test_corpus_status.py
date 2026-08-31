"""Admin corpus management, status, connection tests, memory (D-080).

The corpus endpoint's pipeline is exercised through the same seams the real
flow uses: the real BNS-spec validation rejection is covered by an
integration test against the dev Gazette PDF (BNSS must be rejected as BNS),
and the HTTP contract (auth, atomic activation, failure preserving the old
corpus) is covered with injected fakes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from app.admin.corpus import CorpusReplacementError
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from tests.admin.test_admin import ADMIN, MUTATING, login, make_settings

DEV_PDF = Path(__file__).resolve().parents[3] / "data" / "raw" / "BNS_bare_act_2023.pdf"


class FakeEvidence:
    def __init__(self) -> None:
        self.results = ["chunk-1", "chunk-2"]


class FakeRetrieval:
    def retrieve(self, query: str, *args: Any, **kwargs: Any) -> FakeEvidence:
        return FakeEvidence()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(settings=make_settings(tmp_path))
    app.state.retrieval_service = FakeRetrieval()
    return TestClient(app)


def _pdf_upload(client: TestClient, content: bytes = b"%PDF-1.4 fake") -> Any:
    return client.post(
        "/api/v1/admin/corpus",
        files={"file": ("replacement.pdf", content, "application/pdf")},
        headers=MUTATING,
    )


class TestCorpusAccess:
    def test_requires_auth(self, client: TestClient) -> None:
        assert client.get("/api/v1/admin/corpus").status_code == 401
        assert client.post("/api/v1/admin/corpus").status_code == 401

    def test_requires_mutating_header(self, client: TestClient) -> None:
        login(client)
        response = client.post(
            "/api/v1/admin/corpus",
            files={"file": ("x.pdf", b"%PDF", "application/pdf")},
        )
        assert response.status_code == 401

    def test_rejects_non_pdf(self, client: TestClient) -> None:
        login(client)
        response = client.post(
            "/api/v1/admin/corpus",
            files={"file": ("x.txt", b"hello", "text/plain")},
            headers=MUTATING,
        )
        assert response.status_code == 415

    def test_rejects_empty_upload(self, client: TestClient) -> None:
        login(client)
        response = client.post(
            "/api/v1/admin/corpus",
            files={"file": ("x.pdf", b"", "application/pdf")},
            headers=MUTATING,
        )
        assert response.status_code == 422

    def test_rejects_oversized_upload(self, tmp_path: Path) -> None:
        settings = Settings(
            _env_file=None,
            admin_username=ADMIN["username"],
            admin_password=ADMIN["password"],
            admin_settings_path=str(tmp_path / "admin.json"),
            storage_dir=str(tmp_path / "storage"),
            max_upload_size_mb=1,
        )
        app = create_app(settings=settings)
        with TestClient(app) as client:
            login(client)
            response = client.post(
                "/api/v1/admin/corpus",
                files={"file": ("big.pdf", b"x" * (2 * 1024 * 1024), "application/pdf")},
                headers=MUTATING,
            )
            assert response.status_code == 413


class TestCorpusActivation:
    def test_failed_validation_preserves_active_corpus(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.api.v1.admin as admin_module

        old_service = client.app.state.retrieval_service

        def reject(pdf_bytes: bytes, settings: Settings, *, artifacts_dir: Path) -> Any:
            raise CorpusReplacementError(
                "source does not match expected corpus: title mismatch (BNSS is not BNS)"
            )

        monkeypatch.setattr(admin_module, "build_replacement", reject)
        login(client)
        response = _pdf_upload(client)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CORPUS_REJECTED"
        # Active corpus unchanged.
        assert client.app.state.retrieval_service is old_service

    def test_failed_verification_preserves_active_corpus(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import app.api.v1.admin as admin_module

        old_service = client.app.state.retrieval_service
        artifact = tmp_path / "artifact.jsonl"
        artifact.write_text("{}")

        def ok(pdf_bytes: bytes, settings: Settings, *, artifacts_dir: Path) -> Any:
            manifest = {
                "act": "Bharatiya Nyaya Sanhita, 2023",
                "act_short": "BNS",
                "filename": "bns.pdf",
                "sha256": "abc123",
                "pages": 200,
                "sections": 358,
                "chunks": 900,
                "ingested_at": "2026-08-31T00:00:00+00:00",
                "artifact_path": str(artifact),
            }
            return manifest, artifact

        def bad_verification(service: object) -> None:
            raise CorpusReplacementError(
                "The new corpus failed verification: no results for a core statute query."
            )

        monkeypatch.setattr(admin_module, "build_replacement", ok)
        monkeypatch.setattr(admin_module, "verify_artifact", bad_verification)
        login(client)
        response = _pdf_upload(client)
        assert response.status_code == 500
        assert client.app.state.retrieval_service is old_service
        # The failed artifact is cleaned up.
        assert not artifact.exists()

    def test_successful_replacement_activates_atomically(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import app.api.v1.admin as admin_module

        artifact = tmp_path / "new.jsonl"
        artifact.write_text("{}")
        new_service = FakeRetrieval()

        def ok(pdf_bytes: bytes, settings: Settings, *, artifacts_dir: Path) -> Any:
            return (
                {
                    "act": "Bharatiya Nyaya Sanhita, 2023",
                    "act_short": "BNS",
                    "filename": "bns.pdf",
                    "sha256": "deadbeef",
                    "pages": 200,
                    "sections": 358,
                    "chunks": 900,
                    "ingested_at": "2026-08-31T00:00:00+00:00",
                    "artifact_path": str(artifact),
                },
                artifact,
            )

        monkeypatch.setattr(admin_module, "build_replacement", ok)
        monkeypatch.setattr(admin_module, "_build_retrieval", lambda settings, request: new_service)
        login(client)
        response = _pdf_upload(client)
        assert response.status_code == 200
        assert response.json()["corpus"]["act_short"] == "BNS"
        assert client.app.state.retrieval_service is new_service
        # Manifest visible via GET and persisted.
        info = client.get("/api/v1/admin/corpus").json()
        assert info["sha256"] == "deadbeef"
        import json

        stored = json.loads((tmp_path / "admin.json").read_text())
        assert stored["corpus"]["sha256"] == "deadbeef"


@pytest.mark.skipif(not DEV_PDF.exists(), reason="dev Gazette PDF not present")
def test_real_bnss_pdf_is_rejected_as_bns(tmp_path: Path) -> None:
    """Content-based gate: the BNSS dev fixture must never ingest as BNS."""
    from app.admin.corpus import build_replacement

    settings = Settings(
        _env_file=None,
        admin_settings_path=str(tmp_path / "admin.json"),
        storage_dir=str(tmp_path / "storage"),
    )
    with pytest.raises(CorpusReplacementError) as excinfo:
        build_replacement(
            DEV_PDF.read_bytes()[: 20 * 1024 * 1024], settings, artifacts_dir=tmp_path / "art"
        )
    assert "expected corpus" in str(excinfo.value)


BNS_PDF = Path(__file__).resolve().parents[3] / "data" / "raw" / "BNS_gazette_2023.pdf"


@pytest.mark.skipif(not BNS_PDF.exists(), reason="official BNS Gazette PDF not present")
def test_real_bns_gazette_ingests(tmp_path: Path) -> None:
    """The official Gazette BNS PDF passes the full content-based pipeline."""
    from app.admin.corpus import build_replacement

    settings = Settings(
        _env_file=None,
        admin_settings_path=str(tmp_path / "admin.json"),
        storage_dir=str(tmp_path / "storage"),
    )
    manifest, artifact = build_replacement(
        BNS_PDF.read_bytes(), settings, artifacts_dir=tmp_path / "art"
    )
    assert manifest["act_short"] == "BNS"
    assert manifest["sections"] == 358
    assert artifact.exists()


class TestStatusAndTests:
    def test_status_reports_truthful_states(self, client: TestClient) -> None:
        login(client)
        status = client.get("/api/v1/admin/status").json()
        assert status["backend"]["status"] == "ok"
        # Unreachable local deps are reported as unavailable, never "ok".
        for dep in ("postgres", "redis", "qdrant"):
            assert status[dep]["status"] in ("ok", "unavailable", "error")
        assert status["worker"]["status"] in ("ok", "not_configured", "unavailable")
        assert status["llm"]["provider"] in ("ollama",)

    def test_llm_test_reports_failure_without_leaking(self, client: TestClient) -> None:
        login(client)
        result = client.post("/api/v1/admin/test/llm").json()
        assert set(result) >= {"success", "message"}
        assert "api" not in result.get("message", "").lower()

    def test_stt_tts_test_with_fake_providers(self, tmp_path: Path) -> None:
        from app.speech.base import SynthesisResult, TranscriptionResult
        from app.speech.service import SpeechService

        class FakeSTT:
            async def transcribe(self, data: bytes, *, mime_type: str, language: str | None):
                return TranscriptionResult(text="ok", language="en")

        class FakeTTS:
            async def synthesize(self, text: str, *, language: str):
                return SynthesisResult(audio=b"RIFF", media_type="audio/wav")

        app = create_app(settings=make_settings(tmp_path))
        app.state.speech_service = SpeechService(stt=FakeSTT(), tts=FakeTTS())  # type: ignore[arg-type]
        with TestClient(app) as client:
            login(client)
            assert client.post("/api/v1/admin/test/stt").json()["success"] is True
            assert client.post("/api/v1/admin/test/tts").json()["success"] is True

    def test_tts_test_reports_provider_failure(self, tmp_path: Path) -> None:
        from app.speech.base import SpeechProviderError
        from app.speech.service import SpeechService

        class BrokenTTS:
            async def synthesize(self, text: str, *, language: str):
                raise SpeechProviderError("The text-to-speech provider is not available.")

        app = create_app(settings=make_settings(tmp_path))
        app.state.speech_service = SpeechService(tts=BrokenTTS())  # type: ignore[arg-type]
        with TestClient(app) as client:
            login(client)
            result = client.post("/api/v1/admin/test/tts").json()
            assert result["success"] is False


class TestCorpusPersistence:
    def test_activated_corpus_path_survives_rebuild(self, tmp_path: Path) -> None:
        """After restart, the activated artifact — not the env corpus — loads."""
        from app.admin.store import AdminSettingsStore

        store = AdminSettingsStore(str(tmp_path / "admin.json"))
        artifact = tmp_path / "new.jsonl"
        artifact.write_text("{}")
        store.save(
            {},
            {},
            {
                "act": "Bharatiya Nyaya Sanhita, 2023",
                "act_short": "BNS",
                "artifact_path": str(artifact),
                "sha256": "deadbeef",
            },
        )
        base = Settings(
            _env_file=None,
            admin_settings_path=str(tmp_path / "admin.json"),
            storage_dir=str(tmp_path / "storage"),
            retrieval_corpus_path="/env/bnss-dev.jsonl",
        )
        rebuilt = store.apply_overrides(base)
        assert rebuilt.retrieval_corpus_path == str(artifact)


class TestMemory:
    def test_memory_info_documents_architecture(self, client: TestClient) -> None:
        login(client)
        info = client.get("/api/v1/admin/memory").json()
        assert info["history_untrusted"] is True
        assert info["persistent_server_memory"] is False
        assert info["history_max_turns"] >= 1

    def test_memory_update_changes_history_cap(self, client: TestClient, tmp_path: Path) -> None:
        login(client)
        response = client.put(
            "/api/v1/admin/memory",
            json={"chat_history_max_turns": 5},
            headers=MUTATING,
        )
        assert response.status_code == 200
        assert client.app.state.settings.chat_history_max_turns == 5
