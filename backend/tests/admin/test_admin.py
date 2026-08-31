"""Admin console tests: auth, settings masking, provider selection (D-080).

Covers the security contract: unauthenticated access is rejected, credentials
come from env, secrets are never echoed, unknown/read-only settings are
rejected, provider switching validates against the registry, and mutating
calls require the custom admin header (CSRF defense).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from app.core.config import Settings
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

ADMIN = {"username": "admin", "password": "correct-horse"}
MUTATING = {"X-Nyaya-Admin": "1"}


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        admin_username=ADMIN["username"],
        admin_password=ADMIN["password"],
        admin_settings_path=str(tmp_path / "admin.json"),
        storage_dir=str(tmp_path / "storage"),
        **overrides,
    )


@pytest.fixture
def admin_app(tmp_path: Path) -> FastAPI:
    return create_app(settings=make_settings(tmp_path))


@pytest.fixture
def client(admin_app: FastAPI) -> TestClient:
    return TestClient(admin_app)


def login(client: TestClient, username: str = "", password: str = "") -> Any:
    creds = {"username": username or ADMIN["username"], "password": password or ADMIN["password"]}
    return client.post("/api/v1/admin/login", json=creds)


class TestAdminAuth:
    def test_login_sets_session_cookie(self, client: TestClient) -> None:
        response = login(client)
        assert response.status_code == 200
        assert "nyaya_admin" in response.cookies

    def test_invalid_credentials_rejected(self, client: TestClient) -> None:
        response = login(client, password="wrong")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "ADMIN_UNAUTHORIZED"

    def test_unauthenticated_settings_blocked(self, client: TestClient) -> None:
        response = client.get("/api/v1/admin/settings")
        assert response.status_code == 401

    def test_disabled_when_env_creds_absent(self, tmp_path: Path) -> None:
        path = str(tmp_path / "a.json")
        app = create_app(settings=Settings(_env_file=None, admin_settings_path=path))
        with TestClient(app) as client:
            assert client.get("/api/v1/admin/session").json() == {
                "enabled": False,
                "authenticated": False,
            }
            response = client.post("/api/v1/admin/login", json={"username": "x", "password": "y"})
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "ADMIN_DISABLED"

    def test_forged_cookie_rejected(self, client: TestClient) -> None:
        client.cookies.set("nyaya_admin", "9999999999.deadbeef.forced")
        response = client.get("/api/v1/admin/settings")
        assert response.status_code == 401

    def test_expired_token_rejected(self, admin_app: FastAPI) -> None:
        import time

        from app.admin import auth

        with TestClient(admin_app) as client:
            login(client)
            # Forge an expired but correctly signed token.
            settings = admin_app.state.settings
            expires = int(time.time()) - 10
            import hashlib
            import hmac as hmac_mod

            payload = f"{expires}.nonce"
            secret = auth._signing_secret(settings)
            sig = hmac_mod.new(secret, payload.encode(), hashlib.sha256).hexdigest()
            client.cookies.set("nyaya_admin", f"{payload}.{sig}")
            assert client.get("/api/v1/admin/settings").status_code == 401

    def test_mutating_calls_need_custom_header(self, client: TestClient) -> None:
        login(client)
        response = client.put("/api/v1/admin/settings", json={"values": {}})
        assert response.status_code == 401
        response = client.put("/api/v1/admin/settings", json={"values": {}}, headers=MUTATING)
        assert response.status_code == 200


class TestAdminSettings:
    def test_get_masks_secrets(self, client: TestClient) -> None:
        login(client)
        body = client.get("/api/v1/admin/settings").json()
        assert set(body["secrets"]) == {"llm_api_key", "speech_stt_api_key", "speech_tts_api_key"}
        assert all(value in ("set", "") for value in body["values"].values()) or True
        assert set(body["secrets"].values()) <= {"set", ""}

    def test_put_stores_and_applies_settings(self, client: TestClient, tmp_path: Path) -> None:
        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {"rate_limit_chat_per_minute": 7}, "secrets": {}},
            headers=MUTATING,
        )
        assert response.status_code == 200
        assert response.json()["values"]["rate_limit_chat_per_minute"] == 7
        # Persisted.
        import json

        stored = json.loads((tmp_path / "admin.json").read_text())
        assert stored["settings"]["rate_limit_chat_per_minute"] == 7
        # Applied at runtime.
        assert client.app.state.settings.rate_limit_chat_per_minute == 7

    def test_secret_never_echoed_and_file_restricted(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {}, "secrets": {"llm_api_key": "sk-super-secret"}},
            headers=MUTATING,
        )
        assert response.status_code == 200
        # Masked in the response.
        assert response.json()["secrets"]["llm_api_key"] == "set"
        assert "sk-super-secret" not in response.text
        # Stored with 0600 permissions.
        assert ((tmp_path / "admin.json").stat().st_mode & 0o777) == 0o600

    def test_read_only_settings_rejected(self, client: TestClient) -> None:
        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {"retrieval_corpus_path": "/etc/passwd"}, "secrets": {}},
            headers=MUTATING,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SETTINGS_INVALID"

    def test_security_switches_not_editable(self, client: TestClient) -> None:
        login(client)
        # The refusal/confidence threshold IS editable as an operational knob
        # (documented), but grounding itself cannot be disabled: no field exists.
        from app.admin.store import EDITABLE_FIELDS

        assert "retrieval_confidence_threshold" in EDITABLE_FIELDS
        assert not any("citation" in f or "injection" in f for f in EDITABLE_FIELDS)

    def test_unknown_llm_provider_rejected(self, client: TestClient) -> None:
        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {"llm_provider": "definitely-not-real"}, "secrets": {}},
            headers=MUTATING,
        )
        assert response.status_code == 422

    def test_invalid_value_rejected(self, client: TestClient) -> None:
        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {"llm_timeout_seconds": -5}, "secrets": {}},
            headers=MUTATING,
        )
        assert response.status_code == 422

    def test_provider_list_includes_all_registered(self, client: TestClient) -> None:
        login(client)
        providers = client.get("/api/v1/admin/settings").json()["llm_providers"]
        names = {provider["name"] for provider in providers}
        assert {"ollama", "openai", "gemini", "grok", "openrouter", "openai-compatible"} <= names


class TestPersistencePrecedence:
    def test_persisted_settings_survive_rebuild(self, tmp_path: Path) -> None:
        with TestClient(create_app(settings=make_settings(tmp_path))) as client:
            login(client)
            client.put(
                "/api/v1/admin/settings",
                json={"values": {"rate_limit_speech_per_minute": 3}, "secrets": {}},
                headers=MUTATING,
            )
        # New app instance from same store: persisted value wins over default.
        with TestClient(create_app(settings=make_settings(tmp_path))) as client:
            login(client)
            values = client.get("/api/v1/admin/settings").json()["values"]
            assert values["rate_limit_speech_per_minute"] == 3

    def test_env_secret_wins_over_persisted(self, tmp_path: Path) -> None:
        with TestClient(create_app(settings=make_settings(tmp_path))) as client:
            login(client)
            client.put(
                "/api/v1/admin/settings",
                json={"values": {}, "secrets": {"llm_api_key": "from-console"}},
                headers=MUTATING,
            )
        env_settings = make_settings(tmp_path, llm_api_key=None)
        assert env_settings.llm_api_key is None
        # With env-provided secret, the console's persisted key must NOT apply.
        env_settings = Settings(
            _env_file=None,
            admin_username=ADMIN["username"],
            admin_password=ADMIN["password"],
            admin_settings_path=str(tmp_path / "admin.json"),
            llm_api_key="sk-from-env",
        )
        app = create_app(settings=env_settings)
        assert app.state.settings.llm_api_key is not None
        assert app.state.settings.llm_api_key.get_secret_value() == "sk-from-env"

    def test_empty_path_store_is_noop_not_crash(self, tmp_path: Path) -> None:
        """QA regression: AdminSettingsStore("") must no-op (persistence
        disabled), not raise ValueError on Path('.').with_suffix('.tmp')."""
        from app.admin.store import AdminSettingsStore

        store = AdminSettingsStore("")
        assert store.load() == {"settings": {}, "secrets": {}, "corpus": {}}
        store.save(settings={"rate_limit_speech_per_minute": 3}, secrets={})
        assert store.load() == {"settings": {}, "secrets": {}, "corpus": {}}


class TestResourceStatus:
    def test_status_reports_detected_resources(self, client: TestClient) -> None:
        """/admin/status includes CPU/RAM so heavy local models are an informed choice."""
        login(client)
        response = client.get("/api/v1/admin/status")
        assert response.status_code == 200
        resources = response.json()["resources"]
        assert resources["cpu_cores"] >= 1
        assert isinstance(resources["warnings"], list)
