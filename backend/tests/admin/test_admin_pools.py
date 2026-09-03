"""Admin provider-pool API tests (2026-09 provider failover task).

Covers the pool CRUD contract: auth gating, structural validation,
test-before-save, encrypted per-entry secrets, persistence across
restarts, runtime activation (mode "pool") and the ENV fallback when no
pool is configured.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from app.core.config import Settings
from app.llm.base import ProviderHealth, ProviderHealthState
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.admin.test_admin import ADMIN, MUTATING, login, make_settings

ENTRY_GROQ = {
    "id": "groq-main",
    "provider": "groq",
    "model": "openai/gpt-oss-120b",
    "enabled": True,
    "priority": 10,
}
ENTRY_OLLAMA = {
    "id": "ollama-backup",
    "provider": "ollama",
    "model": "llama3.1",
    "enabled": True,
    "priority": 20,
}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(settings=make_settings(tmp_path))
    with TestClient(app) as test_client:
        login(test_client)
        yield test_client


def _pool_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "pools": {
            "llm": {
                "entries": [ENTRY_GROQ, ENTRY_OLLAMA],
                "default_entry_id": "groq-main",
                "strategy": "priority",
            }
        },
        "secrets": {"pool:llm:groq-main": "gsk-pool-secret"},
        "force": True,
    }
    payload.update(overrides)
    return payload


class TestProviderPoolAPI:
    def test_get_requires_auth(self, tmp_path: Path) -> None:
        app = create_app(settings=make_settings(tmp_path))
        with TestClient(app) as unauth:
            assert unauth.get("/api/v1/admin/providers").status_code == 401

    def test_put_requires_admin_header(self, client: TestClient) -> None:
        response = client.put("/api/v1/admin/providers", json=_pool_payload())
        assert response.status_code == 401

    def test_empty_state_is_environment_mode(self, client: TestClient) -> None:
        body = client.get("/api/v1/admin/providers").json()
        for name in ("llm", "stt", "tts"):
            assert body["pools"][name]["mode"] == "environment"
            assert body["pools"][name]["entries"] == []
        assert client.app.state.provider_pool_runtime.llm is None

    def test_put_activates_pool_runtime(self, client: TestClient) -> None:
        response = client.put(
            "/api/v1/admin/providers", json=_pool_payload(), headers=MUTATING
        )
        assert response.status_code == 200
        body = response.json()
        assert body["pools"]["llm"]["mode"] == "pool"
        assert body["pools"]["llm"]["default_entry_id"] == "groq-main"
        assert body["pools"]["llm"]["strategy"] == "priority"
        assert client.app.state.provider_pool_runtime.llm is not None
        # The default entry is what the runtime presents as primary.
        metadata = client.app.state.provider_pool_runtime.llm.metadata()
        assert metadata.provider == "groq"

    def test_secrets_encrypted_and_masked(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        response = client.put(
            "/api/v1/admin/providers", json=_pool_payload(), headers=MUTATING
        )
        assert response.status_code == 200
        # Never echoed.
        assert "gsk-pool-secret" not in response.text
        body = response.json()
        by_id = {e["id"]: e for e in body["pools"]["llm"]["entries"]}
        assert by_id["groq-main"]["api_key_set"] is True
        assert by_id["ollama-backup"]["api_key_set"] is False
        # Never persisted in plaintext.
        raw = (tmp_path / "admin.json").read_text()
        assert "gsk-pool-secret" not in raw
        stored = json.loads(raw)
        assert "pool:llm:groq-main" in stored["secrets_encrypted"]
        assert stored["provider_pools"]["llm"]["default_entry_id"] == "groq-main"

    def test_unknown_llm_provider_rejected(self, client: TestClient) -> None:
        bad = _pool_payload()
        bad["pools"]["llm"]["entries"] = [
            {**ENTRY_GROQ, "provider": "nonexistent-cloud"}
        ]
        response = client.put("/api/v1/admin/providers", json=bad, headers=MUTATING)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "PROVIDER_POOL_INVALID"

    def test_unknown_speech_provider_rejected(self, client: TestClient) -> None:
        bad = _pool_payload()
        bad["pools"]["stt"] = {
            "entries": [{"id": "s1", "provider": "dragon-speak", "enabled": True}]
        }
        response = client.put("/api/v1/admin/providers", json=bad, headers=MUTATING)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "PROVIDER_POOL_INVALID"

    def test_disabled_default_rejected(self, client: TestClient) -> None:
        bad = _pool_payload()
        bad["pools"]["llm"]["entries"] = [{**ENTRY_GROQ, "enabled": False}, ENTRY_OLLAMA]
        response = client.put("/api/v1/admin/providers", json=bad, headers=MUTATING)
        assert response.status_code == 422
        assert "disabled" in response.json()["error"]["message"]

    def test_duplicate_entry_ids_rejected(self, client: TestClient) -> None:
        bad = _pool_payload()
        bad["pools"]["llm"]["entries"] = [ENTRY_GROQ, ENTRY_GROQ]
        response = client.put("/api/v1/admin/providers", json=bad, headers=MUTATING)
        assert response.status_code == 422

    def test_bad_secret_key_rejected(self, client: TestClient) -> None:
        bad = _pool_payload()
        bad["secrets"] = {"llm_api_key": "not-a-pool-key"}
        response = client.put("/api/v1/admin/providers", json=bad, headers=MUTATING)
        assert response.status_code == 422

    def test_secret_key_for_missing_entry_rejected(self, client: TestClient) -> None:
        bad = _pool_payload()
        bad["secrets"] = {"pool:llm:ghost-entry": "sk-x"}
        response = client.put("/api/v1/admin/providers", json=bad, headers=MUTATING)
        assert response.status_code == 422

    def test_unverified_entry_rejected_nothing_saved(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        class UnhealthyProvider:
            def __init__(self, name: str, settings: Settings) -> None:
                pass

            async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
                return ProviderHealth(
                    state=ProviderHealthState.INVALID_CONFIGURATION,
                    provider="groq",
                    model="openai/gpt-oss-120b",
                    detail="bad key",
                )

        class FakeRegistry:
            def create(self, name: str, settings: Settings) -> UnhealthyProvider:
                return UnhealthyProvider(name, settings)

            def available(self) -> list[str]:
                return ["ollama", "groq", "gemini"]

        client.app.state.llm_registry = FakeRegistry()
        payload = _pool_payload()
        payload["force"] = False
        response = client.put(
            "/api/v1/admin/providers", json=payload, headers=MUTATING
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "PROVIDER_POOL_VERIFY_FAILED"
        assert "groq-main" in response.json()["error"]["message"]
        # Nothing persisted: still environment mode with no pool.
        after = client.get("/api/v1/admin/providers").json()
        assert after["pools"]["llm"]["mode"] == "environment"
        assert client.app.state.provider_pool_runtime.llm is None

    def test_clear_secrets(self, client: TestClient) -> None:
        client.put("/api/v1/admin/providers", json=_pool_payload(), headers=MUTATING)
        response = client.put(
            "/api/v1/admin/providers",
            json=_pool_payload(
                secrets={},
                clear_secrets=["pool:llm:groq-main"],
            ),
            headers=MUTATING,
        )
        assert response.status_code == 200
        by_id = {e["id"]: e for e in response.json()["pools"]["llm"]["entries"]}
        assert by_id["groq-main"]["api_key_set"] is False

    def test_speech_pool_with_browser_entry(self, client: TestClient) -> None:
        """Browser delegation stays a first-class pool citizen (scenario 14)."""
        payload = _pool_payload()
        payload["pools"]["stt"] = {
            "entries": [{"id": "browser-stt", "provider": "browser", "enabled": True}]
        }
        payload["pools"]["tts"] = {
            "entries": [{"id": "browser-tts", "provider": "browser", "enabled": True}]
        }
        response = client.put(
            "/api/v1/admin/providers", json=payload, headers=MUTATING
        )
        assert response.status_code == 200
        assert response.json()["pools"]["stt"]["mode"] == "pool"
        assert response.json()["pools"]["tts"]["mode"] == "pool"
        assert client.app.state.provider_pool_runtime.stt is not None
        assert client.app.state.provider_pool_runtime.tts is not None

    def test_pool_persists_across_restart(self, client: TestClient, tmp_path: Path) -> None:
        client.put("/api/v1/admin/providers", json=_pool_payload(), headers=MUTATING)
        # "Restart": a brand-new app from the same settings path.
        with TestClient(create_app(settings=make_settings(tmp_path))) as fresh:
            login(fresh)
            body = fresh.get("/api/v1/admin/providers").json()
            assert body["pools"]["llm"]["mode"] == "pool"
            assert fresh.app.state.provider_pool_runtime.llm is not None
            by_id = {e["id"]: e for e in body["pools"]["llm"]["entries"]}
            assert by_id["groq-main"]["api_key_set"] is True
            # And the runtime routes to the persisted default.
            assert (
                fresh.app.state.provider_pool_runtime.llm.metadata().provider == "groq"
            )

    def test_health_snapshot_visible(self, client: TestClient) -> None:
        client.put("/api/v1/admin/providers", json=_pool_payload(), headers=MUTATING)
        board = client.app.state.provider_health_board
        board.record_failure(
            "llm", "groq-main", error_class="rate_limit", message="429", cooldown_seconds=30.0
        )
        body = client.get("/api/v1/admin/providers").json()
        by_id = {e["id"]: e for e in body["pools"]["llm"]["entries"]}
        assert by_id["groq-main"]["health"]["state"] == "cooling"
        assert by_id["groq-main"]["health"]["last_error_class"] == "rate_limit"
        assert by_id["ollama-backup"]["health"]["state"] == "untested"

    def test_entry_test_endpoint(self, client: TestClient) -> None:
        class HealthyProvider:
            def __init__(self, name: str, settings: Settings) -> None:
                pass

            async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
                return ProviderHealth(
                    state=ProviderHealthState.HEALTHY,
                    provider="ollama",
                    model="llama3.1",
                )

        class FakeRegistry:
            def create(self, name: str, settings: Settings) -> HealthyProvider:
                return HealthyProvider(name, settings)

            def available(self) -> list[str]:
                return ["ollama", "groq", "gemini"]

        client.app.state.llm_registry = FakeRegistry()
        response = client.post(
            "/api/v1/admin/providers/test",
            json={"pool": "llm", "entry": ENTRY_OLLAMA, "api_key": ""},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
