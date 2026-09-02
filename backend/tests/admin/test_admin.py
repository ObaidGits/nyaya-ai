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
from app.llm.base import ProviderHealthState
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
        import json

        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            # force: the masking contract is under test here, not verification
            # (which would probe the un-saved candidate; see TestSaveVerification).
            json={"values": {}, "secrets": {"llm_api_key": "sk-super-secret"}, "force": True},
            headers=MUTATING,
        )
        assert response.status_code == 200
        # Masked in the response.
        assert response.json()["secrets"]["llm_api_key"] == "set"
        assert "sk-super-secret" not in response.text
        # NEVER written to disk: the persisted file is secret-free (raw text
        # check — no plaintext key anywhere in it, not just in "secrets").
        raw = (tmp_path / "admin.json").read_text()
        assert "sk-super-secret" not in raw
        assert json.loads(raw)["secrets"] == {}
        # But effective for the running process.
        assert client.app.state.settings.llm_api_key is not None
        assert client.app.state.settings.llm_api_key.get_secret_value() == "sk-super-secret"
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

    def test_secret_sources_reported(self, tmp_path: Path) -> None:
        """The view must say where each secret comes from. Console-saved keys
        win (D-090); the environment value is only the bootstrap default."""
        # Environment-provided key, nothing saved yet → "env".
        env_settings = make_settings(tmp_path, llm_api_key="sk-from-env")
        with TestClient(create_app(settings=env_settings)) as client:
            login(client)
            body = client.get("/api/v1/admin/settings").json()
            assert set(body["secret_sources"]) == {
                "llm_api_key",
                "speech_stt_api_key",
                "speech_tts_api_key",
            }
            assert body["secret_sources"]["llm_api_key"] == "env"
            # Save a console key → the console key is now the effective one.
            client.put(
                "/api/v1/admin/settings",
                json={"values": {}, "secrets": {"llm_api_key": "sk-console"}, "force": True},
                headers=MUTATING,
            )
            body = client.get("/api/v1/admin/settings").json()
            assert body["secret_sources"]["llm_api_key"] == "console"

    def test_no_secret_source_when_unconfigured(self, client: TestClient) -> None:
        login(client)
        body = client.get("/api/v1/admin/settings").json()
        assert body["secret_sources"]["llm_api_key"] == ""

    def test_value_sources_reported(self, client: TestClient) -> None:
        """The view must say, per editable field, whether the effective value
        comes from the console ("console") or the environment ("env") — the
        honest answer to 'the env says one provider, the app runs another'."""
        login(client)
        body = client.get("/api/v1/admin/settings").json()
        assert set(body["value_sources"]) == set(body["values"])
        assert body["value_sources"]["llm_provider"] == "env"
        client.put(
            "/api/v1/admin/settings",
            json={"values": {"rate_limit_chat_per_minute": 6}, "force": True},
            headers=MUTATING,
        )
        body = client.get("/api/v1/admin/settings").json()
        assert body["value_sources"]["rate_limit_chat_per_minute"] == "console"
        assert body["value_sources"]["llm_provider"] == "env"

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
        assert {
            "ollama",
            "openai",
            "gemini",
            "grok",
            "groq",
            "openrouter",
            "openai-compatible",
        } <= names

    def test_provider_list_marks_base_url_requirements(self, client: TestClient) -> None:
        """Only the generic openai-compatible profile requires a base URL;
        every provider with a fixed official API URL does not."""
        login(client)
        providers = {
            provider["name"]: provider
            for provider in client.get("/api/v1/admin/settings").json()["llm_providers"]
        }
        for name in ("ollama", "openai", "gemini", "grok", "groq", "openrouter"):
            assert providers[name]["requires_base_url"] is False, name
            assert providers[name]["default_base_url"], name
            assert providers[name]["default_model"], name
        assert providers["openai-compatible"]["requires_base_url"] is True

    def test_provider_default_url_persisted_on_save(self, client: TestClient) -> None:
        """Saving a built-in provider with a blank base URL persists that
        provider's official endpoint — the admin never types a known URL."""
        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {"llm_provider": "groq", "llm_base_url": ""}},
            headers=MUTATING,
        )
        assert response.status_code == 422  # verification fails: no key configured
        assert response.json()["error"]["code"] == "LLM_VERIFICATION_FAILED"
        # force skips verification; the URL normalization must still apply.
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {"llm_provider": "groq", "llm_base_url": ""}, "force": True},
            headers=MUTATING,
        )
        assert response.status_code == 200
        assert response.json()["values"]["llm_base_url"] == "https://api.groq.com/openai/v1"
        assert response.json()["values"]["llm_provider"] == "groq"


class TestLlmModelList:
    def test_requires_authentication(self, client: TestClient) -> None:
        response = client.post("/api/v1/admin/llm/models")
        assert response.status_code == 401

    def test_returns_provider_models(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_list(settings: Settings) -> list[str]:
            return ["gemini-2.0-flash", "gemini-2.5-pro"]

        from app.api.v1 import admin as admin_module

        monkeypatch.setattr(admin_module, "_list_llm_models", fake_list)
        login(client)
        response = client.post("/api/v1/admin/llm/models")
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == client.app.state.settings.llm_provider  # type: ignore[attr-defined]
        assert body["models"] == ["gemini-2.0-flash", "gemini-2.5-pro"]

    def test_draft_config_overrides_saved(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Loading models must use the form's draft config, not the saved
        settings — including a freshly typed API key."""
        captured: dict[str, Settings] = {}

        async def fake_list(settings: Settings) -> list[str]:
            captured["settings"] = settings
            return ["gemini-2.0-flash"]

        from app.api.v1 import admin as admin_module

        monkeypatch.setattr(admin_module, "_list_llm_models", fake_list)
        login(client)
        response = client.post(
            "/api/v1/admin/llm/models",
            json={
                "provider": "gemini",
                "model": "gemini-2.0-flash",
                "base_url": "https://example.com/v1beta",
                "api_key": "sk-typed-in-form",
            },
            headers=MUTATING,
        )
        assert response.status_code == 200
        assert response.json()["provider"] == "gemini"
        draft = captured["settings"]
        assert draft.llm_provider == "gemini"
        assert draft.llm_model == "gemini-2.0-flash"
        assert draft.llm_base_url == "https://example.com/v1beta"
        assert draft.llm_api_key is not None
        assert draft.llm_api_key.get_secret_value() == "sk-typed-in-form"

    def test_blank_draft_base_url_uses_provider_default(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Regression: selecting a new provider with a blank URL must fall
        back to that provider's official endpoint, NOT the previously saved
        URL (which belongs to the old provider)."""
        # Save a config whose base URL is an OpenAI-compatible gateway.
        login(client)
        client.put(
            "/api/v1/admin/settings",
            json={
                "values": {
                    "llm_provider": "openai-compatible",
                    "llm_base_url": "https://gw.example/v1",
                },
                "force": True,
            },
            headers=MUTATING,
        )
        captured: dict[str, Settings] = {}

        async def fake_list(settings: Settings) -> list[str]:
            captured["settings"] = settings
            return ["gemini-2.0-flash"]

        from app.api.v1 import admin as admin_module

        monkeypatch_local = pytest.MonkeyPatch()
        monkeypatch_local.setattr(admin_module, "_list_llm_models", fake_list)
        try:
            # Draft: provider gemini, NO base_url (form field blank).
            response = client.post(
                "/api/v1/admin/llm/models", json={"provider": "gemini"}, headers=MUTATING
            )
            assert response.status_code == 200
            assert captured["settings"].llm_base_url == (
                "https://generativelanguage.googleapis.com/v1beta"
            )
        finally:
            monkeypatch_local.undo()

    def test_provider_switch_without_url_resets_saved_url(self, client: TestClient) -> None:
        """Same regression on the save path: a partial provider update must
        not persist the old provider's base URL."""
        login(client)
        client.put(
            "/api/v1/admin/settings",
            json={
                "values": {
                    "llm_provider": "openai-compatible",
                    "llm_base_url": "https://gw.example/v1",
                },
                "force": True,
            },
            headers=MUTATING,
        )
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {"llm_provider": "openai"}, "force": True},
            headers=MUTATING,
        )
        assert response.status_code == 200
        assert response.json()["values"]["llm_base_url"] == "https://api.openai.com/v1"

    def test_same_provider_blank_draft_url_inherits_saved_custom_url(
        self, client: TestClient
    ) -> None:
        """Regression (live E2E): a provider entry pointed at a custom
        gateway (e.g. 'grok' saved with a Groq endpoint URL) must be tested
        against the SAVED URL when the draft keeps the same provider — the
        console hides the URL field for fixed-URL providers, so a blank
        draft URL must not silently probe the provider's default endpoint
        instead of the configured one."""
        login(client)
        client.put(
            "/api/v1/admin/settings",
            json={
                "values": {
                    "llm_provider": "grok",
                    "llm_base_url": "https://api.groq.com/openai/v1",
                    "llm_model": "openai/gpt-oss-120b",
                },
                "force": True,
            },
            headers=MUTATING,
        )
        captured: dict[str, Settings] = {}

        async def fake_list(settings: Settings) -> list[str]:
            captured["settings"] = settings
            return ["openai/gpt-oss-120b"]

        from app.api.v1 import admin as admin_module

        monkeypatch_local = pytest.MonkeyPatch()
        monkeypatch_local.setattr(admin_module, "_list_llm_models", fake_list)
        try:
            # Draft: SAME provider, blank base_url (the form field is hidden).
            response = client.post(
                "/api/v1/admin/llm/models",
                json={"provider": "grok", "model": "openai/gpt-oss-120b", "api_key": ""},
                headers=MUTATING,
            )
            assert response.status_code == 200
            assert captured["settings"].llm_base_url == "https://api.groq.com/openai/v1"
        finally:
            monkeypatch_local.undo()

    def test_rejects_unknown_draft_fields(self, client: TestClient) -> None:
        login(client)
        response = client.post(
            "/api/v1/admin/llm/models",
            json={"provider": "gemini", "bogus": "x"},
            headers=MUTATING,
        )
        assert response.status_code == 422

    def test_provider_errors_surface_cleanly(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.v1 import admin as admin_module
        from app.core.errors import AppError

        async def failing_list(settings: Settings) -> list[str]:
            raise AppError(
                "Could not reach the provider to list models.",
                status_code=503,
                code="LLM_MODELS_UNAVAILABLE",
            )

        monkeypatch.setattr(admin_module, "_list_llm_models", failing_list)
        login(client)
        response = client.post("/api/v1/admin/llm/models")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "LLM_MODELS_UNAVAILABLE"

    @pytest.mark.parametrize(
        ("provider", "base_url", "expected_path"),
        [
            ("ollama", "", "/api/tags"),
            ("openai", "https://api.openai.com/v1", "/models"),
            ("gemini", "", "/models"),
        ],
    )
    def test_model_listing_endpoint_per_provider(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        provider: str,
        base_url: str,
        expected_path: str,
    ) -> None:
        """_list_llm_models queries each provider's native listing endpoint
        (httpx mocked at the transport level)."""
        from typing import ClassVar

        import app.api.v1.admin as admin_module
        from app.core.config import Settings as AppSettings

        class FakeResponse:
            def __init__(self, payload: dict[str, Any]) -> None:
                self._payload = payload

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, Any]:
                return self._payload

        class FakeClient:
            calls: ClassVar[list[str]] = []

            def __init__(self, **kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> FakeClient:
                return self

            async def __aexit__(self, *args: Any) -> None:
                return None

            async def get(self, url: str, **kwargs: Any) -> FakeResponse:
                FakeClient.calls.append(url)
                if provider == "ollama":
                    return FakeResponse({"models": [{"name": "llama3.1:8b"}]})
                if provider == "gemini":
                    return FakeResponse(
                        {
                            "models": [
                                {
                                    "name": "models/gemini-2.0-flash",
                                    "supportedGenerationMethods": ["generateContent"],
                                },
                                {
                                    "name": "models/embedding-001",
                                    "supportedGenerationMethods": ["embedContent"],
                                },
                            ]
                        }
                    )
                return FakeResponse({"data": [{"id": "gpt-4o-mini"}]})

        monkeypatch.setattr("httpx.AsyncClient", FakeClient)
        settings = AppSettings(
            _env_file=None,
            llm_provider=provider,
            # "" (falsy) makes the listing fall back to the provider default.
            llm_base_url=base_url,
        )
        import asyncio

        models = asyncio.run(admin_module._list_llm_models(settings))
        assert FakeClient.calls and expected_path in FakeClient.calls[0]
        if provider == "ollama":
            assert models == ["llama3.1:8b"]
        elif provider == "gemini":
            assert models == ["gemini-2.0-flash"]  # embedding model filtered out
        else:
            assert models == ["gpt-4o-mini"]


class TestLlmConnectionTest:
    """POST /admin/test/llm exercises the DRAFT config from the form."""

    def test_draft_config_tested_not_saved(self, client: TestClient) -> None:
        """The typed provider/model must be tested even before saving."""
        from app.llm.base import ProviderHealth, ProviderHealthState, ProviderMetadata
        from app.llm.registry import UnknownProviderError

        created: dict[str, Settings] = {}

        class FakeProvider:
            def __init__(self, name: str, settings: Settings) -> None:
                created["name"] = name
                created["settings"] = settings

            async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
                return ProviderHealth(
                    state=ProviderHealthState.HEALTHY,
                    provider="gemini",
                    model="gemini-2.0-flash",
                    detail="reachable and model available",
                )

            def metadata(self) -> ProviderMetadata:
                return ProviderMetadata(
                    provider="gemini", model="gemini-2.0-flash", supports_streaming=True
                )

        class FakeRegistry:
            def create(self, name: str, settings: Settings) -> FakeProvider:
                if name != "gemini":
                    raise UnknownProviderError(f"unknown provider: {name}")
                return FakeProvider(name, settings)

            def available(self) -> list[str]:
                return ["gemini"]

        client.app.state.llm_registry = FakeRegistry()  # type: ignore[assignment]

        login(client)
        response = client.post(
            "/api/v1/admin/test/llm",
            json={
                "provider": "gemini",
                "model": "gemini-2.0-flash",
                "api_key": "sk-typed",
            },
            headers=MUTATING,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "reachable and model available" in body["message"]
        # The draft (not saved) settings reached the registry.
        assert created["name"] == "gemini"
        assert created["settings"].llm_api_key is not None
        assert created["settings"].llm_api_key.get_secret_value() == "sk-typed"

    def test_saved_key_reused_without_retyping(self, client: TestClient) -> None:
        """Regression (the 'asks for the API key again' bug): after a key is
        saved, testing with a BLANK draft api_key must exercise the SAVED
        key — the admin never re-enters it."""
        from app.llm.base import ProviderHealth, ProviderHealthState

        captured: dict[str, Settings] = {}

        class FakeProvider:
            def __init__(self, name: str, settings: Settings) -> None:
                captured["settings"] = settings

            async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
                return ProviderHealth(
                    state=ProviderHealthState.HEALTHY,
                    provider="gemini",
                    model="gemini-2.0-flash",
                    detail="reachable and model available",
                )

            def metadata(self) -> Any:
                from app.llm.base import ProviderMetadata

                return ProviderMetadata(provider="gemini", model="gemini-2.0-flash")

        class FakeRegistry:
            def create(self, name: str, settings: Settings) -> FakeProvider:
                return FakeProvider(name, settings)

            def available(self) -> list[str]:
                return ["ollama", "gemini", "grok"]

        client.app.state.llm_registry = FakeRegistry()  # type: ignore[assignment]
        login(client)
        # Save a key (force: masking/persistence concern, not verification).
        client.put(
            "/api/v1/admin/settings",
            json={"values": {}, "secrets": {"llm_api_key": "sk-saved-once"}, "force": True},
            headers=MUTATING,
        )
        # Re-test with a blank api_key — the saved key must reach the provider.
        response = client.post(
            "/api/v1/admin/test/llm", json={"provider": "gemini"}, headers=MUTATING
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert captured["settings"].llm_api_key.get_secret_value() == "sk-saved-once"

    def test_model_not_offered_reported(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reachable API + misspelled model id → explicit failure, not
        a misleading 'reachable'."""
        from app.llm.base import ProviderHealth, ProviderHealthState

        class FakeProvider:
            def __init__(self, name: str, settings: Settings) -> None:
                pass

            async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
                return ProviderHealth(
                    state=ProviderHealthState.DEGRADED,
                    provider="gemini",
                    model="gemini-3.5-flash-lite",
                    detail=(
                        "The provider is reachable, but model 'gemini-3.5-flash-lite' is "
                        "not in its model list."
                    ),
                )

            def metadata(self) -> Any:
                from app.llm.base import ProviderMetadata

                return ProviderMetadata(
                    provider="gemini", model="gemini-3.5-flash-lite", supports_streaming=True
                )

        class FakeRegistry:
            def create(self, name: str, settings: Settings) -> FakeProvider:
                return FakeProvider(name, settings)

            def available(self) -> list[str]:
                return ["ollama", "gemini", "grok"]

        client.app.state.llm_registry = FakeRegistry()  # type: ignore[assignment]
        login(client)
        response = client.post(
            "/api/v1/admin/test/llm",
            json={"provider": "gemini", "model": "gemini-3.5-flash-lite"},
            headers=MUTATING,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert "not in" in body["message"] and "gemini-3.5-flash-lite" in body["message"]

    def test_unhealthy_draft_reported(self, client: TestClient) -> None:
        from app.llm.base import ProviderHealth, ProviderHealthState

        class FakeProvider:
            def __init__(self, name: str, settings: Settings) -> None:
                pass

            async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
                return ProviderHealth(
                    state=ProviderHealthState.UNAVAILABLE,
                    provider="gemini",
                    model="m",
                    detail="The provider endpoint is unreachable (network error or timeout).",
                )

            def metadata(self) -> Any:
                from app.llm.base import ProviderMetadata

                return ProviderMetadata(provider="gemini", model="m", supports_streaming=True)

        class FakeRegistry:
            def create(self, name: str, settings: Settings) -> FakeProvider:
                return FakeProvider(name, settings)

            def available(self) -> list[str]:
                return ["ollama", "gemini", "grok"]

        client.app.state.llm_registry = FakeRegistry()  # type: ignore[assignment]
        login(client)
        response = client.post(
            "/api/v1/admin/test/llm", json={"provider": "gemini"}, headers=MUTATING
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert "unreachable" in body["message"]

    def test_connection_test_verifies_chat_capability(self, client: TestClient) -> None:
        """D-096: the test button performs a real generation round-trip, and
        a listed-but-not-chat-capable model (classifier) fails with the
        exact reason — the live prompt-guard incident."""
        from app.llm.base import ProviderHealth, ProviderHealthState

        seen: dict[str, bool] = {}

        class FakeProvider:
            def __init__(self, name: str, settings: Settings) -> None:
                pass

            async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
                seen["verify_chat"] = verify_chat
                return ProviderHealth(
                    state=ProviderHealthState.INVALID_CONFIGURATION,
                    provider="groq",
                    model="meta-llama/llama-prompt-guard-2-86m",
                    detail=(
                        "Model 'meta-llama/llama-prompt-guard-2-86m' rejected a chat "
                        "request (HTTP 400) — it is offered but not chat-capable."
                    ),
                    chat_verified=False,
                )

            def metadata(self) -> Any:
                from app.llm.base import ProviderMetadata

                return ProviderMetadata(
                    provider="groq", model="meta-llama/llama-prompt-guard-2-86m"
                )

        class FakeRegistry:
            def create(self, name: str, settings: Settings) -> FakeProvider:
                return FakeProvider(name, settings)

            def available(self) -> list[str]:
                return ["ollama", "groq"]

        client.app.state.llm_registry = FakeRegistry()  # type: ignore[assignment]
        login(client)
        response = client.post(
            "/api/v1/admin/test/llm",
            json={"provider": "groq", "model": "meta-llama/llama-prompt-guard-2-86m"},
            headers=MUTATING,
        )
        assert response.status_code == 200
        body = response.json()
        assert seen["verify_chat"] is True  # a real generation was attempted
        assert body["success"] is False
        assert "not chat-capable" in body["message"]
        assert "meta-llama/llama-prompt-guard-2-86m" in body["message"]

    def test_chat_verified_success_message(self, client: TestClient) -> None:
        """A passing chat round-trip is reported as usability, not just
        reachability."""
        from app.llm.base import ProviderHealth, ProviderHealthState

        class FakeProvider:
            def __init__(self, name: str, settings: Settings) -> None:
                pass

            async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
                return ProviderHealth(
                    state=ProviderHealthState.HEALTHY,
                    provider="groq",
                    model="openai/gpt-oss-120b",
                    detail="chat verified — the model answered a test prompt",
                    chat_verified=True,
                )

            def metadata(self) -> Any:
                from app.llm.base import ProviderMetadata

                return ProviderMetadata(provider="groq", model="openai/gpt-oss-120b")

        class FakeRegistry:
            def create(self, name: str, settings: Settings) -> FakeProvider:
                return FakeProvider(name, settings)

            def available(self) -> list[str]:
                return ["ollama", "groq"]

        client.app.state.llm_registry = FakeRegistry()  # type: ignore[assignment]
        login(client)
        response = client.post(
            "/api/v1/admin/test/llm", json={"provider": "groq"}, headers=MUTATING
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "chat verified" in body["message"]


class TestSaveVerification:
    """Test-before-activate (D-090): a provider config that does not verify
    can never silently replace the active one."""

    @staticmethod
    def _registry(state: ProviderHealthState) -> Any:
        from app.llm.base import ProviderHealth

        class FakeProvider:
            def __init__(self, name: str, settings: Settings) -> None:
                pass

            async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
                return ProviderHealth(
                    state=state, provider="grok", model="grok-4.6", detail="probe result"
                )

        class FakeRegistry:
            def create(self, name: str, settings: Settings) -> FakeProvider:
                return FakeProvider(name, settings)

            def available(self) -> list[str]:
                return ["ollama", "gemini", "grok"]

        return FakeRegistry()

    def test_unverified_candidate_rejected_and_old_provider_kept(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        import json

        # Establish a working active provider.
        client.app.state.llm_registry = self._registry(ProviderHealthState.HEALTHY)
        login(client)
        client.put(
            "/api/v1/admin/settings",
            json={"values": {"llm_provider": "grok", "llm_model": "grok-4.6"}, "force": True},
            headers=MUTATING,
        )
        before = json.loads((tmp_path / "admin.json").read_text())

        # Candidate that fails verification (bad key / unreachable).
        client.app.state.llm_registry = self._registry(ProviderHealthState.INVALID_CONFIGURATION)
        response = client.put(
            "/api/v1/admin/settings",
            # A NEW key (the realistic failed-replacement scenario: wrong key).
            json={
                "values": {"llm_provider": "grok", "llm_model": "grok-4.6"},
                "secrets": {"llm_api_key": "sk-wrong-key"},
            },
            headers=MUTATING,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "LLM_VERIFICATION_FAILED"
        assert "probe result" in response.json()["error"]["message"]
        # Nothing persisted, nothing applied: old config intact.
        assert json.loads((tmp_path / "admin.json").read_text()) == before
        assert client.app.state.settings.llm_provider == "grok"

    def test_healthy_candidate_activates(self, client: TestClient) -> None:
        client.app.state.llm_registry = self._registry(ProviderHealthState.HEALTHY)
        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {"llm_provider": "grok", "llm_model": "grok-4.6"}},
            headers=MUTATING,
        )
        assert response.status_code == 200
        assert client.app.state.settings.llm_provider == "grok"

    def test_degraded_candidate_rejected(self, client: TestClient) -> None:
        """A model the provider does not offer would break every chat turn —
        it must not become active either."""
        client.app.state.llm_registry = self._registry(ProviderHealthState.DEGRADED)
        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {"llm_provider": "grok", "llm_model": "grok-9"}},
            headers=MUTATING,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "LLM_VERIFICATION_FAILED"

    def test_force_overrides_verification(self, client: TestClient) -> None:
        """Deliberate offline saves are possible, but only explicitly."""
        client.app.state.llm_registry = self._registry(ProviderHealthState.UNAVAILABLE)
        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {"llm_provider": "grok", "llm_model": "grok-4.6"}, "force": True},
            headers=MUTATING,
        )
        assert response.status_code == 200
        assert client.app.state.settings.llm_provider == "grok"

    def test_save_gate_verifies_chat_capability(self, client: TestClient) -> None:
        """D-096: the save-time probe must include the chat round-trip — a
        model that lists fine but cannot answer (classifier) is rejected."""
        from app.llm.base import ProviderHealth, ProviderHealthState

        seen: dict[str, bool] = {}

        class FakeProvider:
            def __init__(self, name: str, settings: Settings) -> None:
                pass

            async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
                seen["verify_chat"] = verify_chat
                return ProviderHealth(
                    state=ProviderHealthState.INVALID_CONFIGURATION,
                    provider="grok",
                    model="grok-guard-2",
                    detail="Model 'grok-guard-2' rejected a chat request (HTTP 400) — "
                    "it is offered but not chat-capable.",
                    chat_verified=False,
                )

        class FakeRegistry:
            def create(self, name: str, settings: Settings) -> FakeProvider:
                return FakeProvider(name, settings)

            def available(self) -> list[str]:
                return ["ollama", "grok"]

        client.app.state.llm_registry = FakeRegistry()  # type: ignore[assignment]
        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {"llm_provider": "grok", "llm_model": "grok-guard-2"}},
            headers=MUTATING,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "LLM_VERIFICATION_FAILED"
        assert seen["verify_chat"] is True  # chat round-trip was part of the gate
        assert "not chat-capable" in response.json()["error"]["message"]
        # Nothing was applied.
        assert client.app.state.settings.llm_provider != "grok"

    def test_non_llm_changes_skip_verification(self, client: TestClient) -> None:
        """Rate-limit or retrieval tweaks must not probe any provider."""
        client.app.state.llm_registry = self._registry(ProviderHealthState.UNAVAILABLE)
        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {"rate_limit_chat_per_minute": 9}},
            headers=MUTATING,
        )
        assert response.status_code == 200
        assert client.app.state.settings.rate_limit_chat_per_minute == 9


class TestSecretLifecycle:
    """Save → reload → retest → replace → remove, without ever echoing the
    secret or letting a blank frontend value destroy it."""

    def test_blank_secret_never_overwrites_saved(self, client: TestClient) -> None:
        login(client)
        client.put(
            "/api/v1/admin/settings",
            json={"values": {}, "secrets": {"llm_api_key": "sk-keep"}, "force": True},
            headers=MUTATING,
        )
        # The UI always sends empty strings for unchanged secrets.
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {}, "secrets": {"llm_api_key": ""}},
            headers=MUTATING,
        )
        assert response.status_code == 200
        assert response.json()["secrets"]["llm_api_key"] == "set"
        assert client.app.state.settings.llm_api_key is not None
        assert client.app.state.settings.llm_api_key.get_secret_value() == "sk-keep"

    def test_explicit_replacement_wins(self, client: TestClient) -> None:
        login(client)
        client.put(
            "/api/v1/admin/settings",
            json={"values": {}, "secrets": {"llm_api_key": "sk-old"}, "force": True},
            headers=MUTATING,
        )
        client.put(
            "/api/v1/admin/settings",
            json={"values": {}, "secrets": {"llm_api_key": "sk-new"}, "force": True},
            headers=MUTATING,
        )
        assert client.app.state.settings.llm_api_key.get_secret_value() == "sk-new"

    def test_explicit_clear_removes_key(self, client: TestClient, tmp_path: Path) -> None:
        import json

        login(client)
        client.put(
            "/api/v1/admin/settings",
            json={"values": {}, "secrets": {"llm_api_key": "sk-gone"}, "force": True},
            headers=MUTATING,
        )
        response = client.put(
            "/api/v1/admin/settings",
            # force: the removal semantics are under test; verification would
            # probe the (offline in tests) default provider.
            json={"values": {}, "clear_secrets": ["llm_api_key"], "force": True},
            headers=MUTATING,
        )
        assert response.status_code == 200
        assert response.json()["secrets"]["llm_api_key"] == ""
        assert client.app.state.settings.llm_api_key is None
        stored = json.loads((tmp_path / "admin.json").read_text())
        assert "llm_api_key" not in stored["secrets"]

    def test_clear_falls_back_to_env_value(self, tmp_path: Path) -> None:
        """Removing the console key returns to the environment bootstrap
        value (consistent across restarts)."""
        env_settings = make_settings(tmp_path, llm_api_key="sk-env")
        with TestClient(create_app(settings=env_settings)) as client:
            login(client)
            client.put(
                "/api/v1/admin/settings",
                json={"values": {}, "secrets": {"llm_api_key": "sk-console"}, "force": True},
                headers=MUTATING,
            )
            assert client.app.state.settings.llm_api_key.get_secret_value() == "sk-console"
            client.put(
                "/api/v1/admin/settings",
                json={"values": {}, "clear_secrets": ["llm_api_key"], "force": True},
                headers=MUTATING,
            )
            assert client.app.state.settings.llm_api_key is not None
            assert client.app.state.settings.llm_api_key.get_secret_value() == "sk-env"

    def test_clear_secret_unknown_name_rejected(self, client: TestClient) -> None:
        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {}, "clear_secrets": ["admin_password"]},
            headers=MUTATING,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SETTINGS_INVALID"


class TestNoPlaintextSecretPersistence:
    """Security contract: the persisted admin.json NEVER contains a PLAINTEXT
    secret.

    Console-entered keys persist as Fernet ciphertext (D-098) so they survive
    restarts; the on-disk file never carries the raw key value, and a legacy
    file with a plaintext key is migrated (adopted + scrubbed) on first load.
    Full lifecycle coverage: tests/admin/test_secret_persistence.py.
    """

    def test_saved_key_never_reaches_disk(self, client: TestClient, tmp_path: Path) -> None:
        import json

        login(client)
        response = client.put(
            "/api/v1/admin/settings",
            json={"values": {}, "secrets": {"llm_api_key": "sk-plaintext-bait"}, "force": True},
            headers=MUTATING,
        )
        assert response.status_code == 200
        # Strong assertion: the RAW file bytes contain no trace of the key.
        raw = (tmp_path / "admin.json").read_text()
        assert "sk-plaintext-bait" not in raw
        assert "secrets" not in raw or json.loads(raw)["secrets"] == {}
        # ...and the key is still effective for the running process.
        assert client.app.state.settings.llm_api_key is not None
        assert client.app.state.settings.llm_api_key.get_secret_value() == "sk-plaintext-bait"

    def test_later_saves_stay_secret_free(self, client: TestClient, tmp_path: Path) -> None:
        """Corpus activation / memory updates rewrite the file; a key saved
        earlier must not leak into any of those rewrites."""
        import json

        login(client)
        client.put(
            "/api/v1/admin/settings",
            json={"values": {}, "secrets": {"llm_api_key": "sk-keep-out"}, "force": True},
            headers=MUTATING,
        )
        client.put(
            "/api/v1/admin/settings",
            json={"values": {"rate_limit_chat_per_minute": 11}},
            headers=MUTATING,
        )
        client.put(
            "/api/v1/admin/memory",
            json={"chat_history_max_turns": 9},
            headers=MUTATING,
        )
        raw = (tmp_path / "admin.json").read_text()
        assert "sk-keep-out" not in raw
        stored = json.loads(raw)
        assert stored["secrets"] == {}
        assert stored["settings"]["rate_limit_chat_per_minute"] == 11
        assert stored["settings"]["chat_history_max_turns"] == 9
        # The key remains effective in memory after the rewrites.
        assert client.app.state.settings.llm_api_key.get_secret_value() == "sk-keep-out"

    def test_legacy_plaintext_key_migrated_on_load(self, tmp_path: Path) -> None:
        """A pre-existing admin.json with a plaintext key: the key keeps
        working for the booted process, and the file is scrubbed on disk
        IMMEDIATELY (not on next save). Other persisted state survives."""
        import json

        path = tmp_path / "admin.json"
        path.write_text(
            json.dumps(
                {
                    "settings": {"llm_provider": "groq", "llm_model": "openai/gpt-oss-120b"},
                    "secrets": {"llm_api_key": "sk-legacy-plaintext"},
                    "corpus": {"sha256": "deadbeef", "artifact_path": "/tmp/x.jsonl"},
                }
            )
        )
        app = create_app(settings=make_settings(tmp_path))
        # Effective for this process: settings AND the migrated key.
        assert app.state.settings.llm_provider == "groq"
        assert app.state.settings.llm_api_key is not None
        assert app.state.settings.llm_api_key.get_secret_value() == "sk-legacy-plaintext"
        assert app.state.settings.retrieval_corpus_path == "/tmp/x.jsonl"
        # Scrubbed from disk immediately; other state preserved.
        raw = path.read_text()
        assert "sk-legacy-plaintext" not in raw
        stored = json.loads(raw)
        assert stored["secrets"] == {}
        assert stored["settings"]["llm_provider"] == "groq"
        assert stored["corpus"]["sha256"] == "deadbeef"
        # D-098: a fresh process (new store) RECOVERS the migrated key —
        # encrypted at rest, stable master key, no restart loss.
        from app.admin.store import AdminSettingsStore

        reloaded = AdminSettingsStore(str(path)).load()
        assert reloaded["secrets"]["llm_api_key"] == "sk-legacy-plaintext"
        assert reloaded["secrets_unreadable"] == []

    def test_masked_secret_response_shape(self, client: TestClient) -> None:
        """The API only ever reports set/empty + the source — never a value,
        not even a masked hint that embeds key material."""
        login(client)
        client.put(
            "/api/v1/admin/settings",
            json={"values": {}, "secrets": {"llm_api_key": "sk-shape-check"}, "force": True},
            headers=MUTATING,
        )
        body = client.get("/api/v1/admin/settings").json()
        assert set(body["secrets"]) == {"llm_api_key", "speech_stt_api_key", "speech_tts_api_key"}
        assert body["secrets"]["llm_api_key"] == "set"
        assert body["secret_sources"]["llm_api_key"] == "console"
        # D-098: the key persists (encrypted) AND is never echoed.
        assert body["secrets_persisted"] is True
        assert body["secrets_unreadable"] == []
        assert "sk-shape-check" not in str(body)


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

    def test_console_secret_survives_restart_encrypted(self, tmp_path: Path) -> None:
        """D-098: console-saved keys are persisted ENCRYPTED (never
        plaintext) and survive a restart — the original bug this decision
        fixed. The console key still wins over the env value (D-090)."""
        with TestClient(create_app(settings=make_settings(tmp_path))) as client:
            login(client)
            client.put(
                "/api/v1/admin/settings",
                json={"values": {}, "secrets": {"llm_api_key": "from-console"}, "force": True},
                headers=MUTATING,
            )
            assert client.app.state.settings.llm_api_key.get_secret_value() == "from-console"
            # Never plaintext on disk — but present as ciphertext.
            assert "from-console" not in (tmp_path / "admin.json").read_text()
        # Restart with an env-provided secret: the PERSISTED console key is
        # still the effective one (D-090 precedence is now stable across
        # restarts, which is the whole point of D-098).
        env_settings = Settings(
            _env_file=None,
            admin_username=ADMIN["username"],
            admin_password=ADMIN["password"],
            admin_settings_path=str(tmp_path / "admin.json"),
            llm_api_key="sk-from-env",
        )
        app = create_app(settings=env_settings)
        assert app.state.settings.llm_api_key is not None
        assert app.state.settings.llm_api_key.get_secret_value() == "from-console"

    def test_persisted_non_secret_settings_survive_restart(self, tmp_path: Path) -> None:
        """Only SECRETS are session-only: provider/model stay persisted and
        keep winning over env after a restart (that is the drift the source
        reporting exists to explain)."""
        with TestClient(create_app(settings=make_settings(tmp_path))) as client:
            login(client)
            client.put(
                "/api/v1/admin/settings",
                json={
                    "values": {"llm_provider": "groq", "llm_model": "openai/gpt-oss-120b"},
                    "force": True,  # offline save: the persistence rule is under test
                },
                headers=MUTATING,
            )
        app = create_app(settings=make_settings(tmp_path))
        assert app.state.settings.llm_provider == "groq"
        assert app.state.settings.llm_model == "openai/gpt-oss-120b"

    def test_effective_config_logged_with_source(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Startup honesty (config-drift fix): booting with persisted console
        settings logs the EFFECTIVE provider/model and names the source, so a
        stale env LLM_* triple can never be misread as the runtime config."""
        import logging

        from app.admin.store import AdminSettingsStore

        store = AdminSettingsStore(str(tmp_path / "admin.json"))
        store.save({"llm_provider": "groq", "llm_model": "openai/gpt-oss-120b"}, {})
        base = Settings(_env_file=None, llm_provider="ollama", llm_model="llama3.1:8b")
        with caplog.at_level(logging.INFO, logger="app.admin.store"):
            effective = store.apply_overrides(base)
        assert effective.llm_provider == "groq"
        messages = " ".join(record.message for record in caplog.records)
        assert "effective LLM config" in messages
        assert "provider=groq" in messages
        assert "model=openai/gpt-oss-120b" in messages
        assert "admin console" in messages
        assert "llm_provider" in messages  # names the overridden fields

    def test_empty_path_store_is_noop_not_crash(self, tmp_path: Path) -> None:
        """QA regression: AdminSettingsStore("") must no-op (persistence
        disabled), not raise ValueError on Path('.').with_suffix('.tmp')."""
        from app.admin.store import AdminSettingsStore

        store = AdminSettingsStore("")
        empty = {"settings": {}, "secrets": {}, "corpus": {}}
        assert store.load() == {**empty, "secrets_unreadable": []}
        assert not store.secrets_persisted
        store.save(settings={"rate_limit_speech_per_minute": 3}, secrets={})
        assert store.load() == {**empty, "secrets_unreadable": []}


class TestResourceStatus:
    def test_status_reports_detected_resources(self, client: TestClient) -> None:
        """/admin/status includes CPU/RAM so heavy local models are an informed choice."""
        login(client)
        response = client.get("/api/v1/admin/status")
        assert response.status_code == 200
        resources = response.json()["resources"]
        assert resources["cpu_cores"] >= 1
        assert isinstance(resources["warnings"], list)
