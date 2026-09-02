"""Encrypted secret persistence regression tests (DECISIONS.md D-098).

The original bug: console-entered API keys lived in process memory only
(``_runtime_secrets``) and ``_write()`` hard-coded an empty secrets section —
any process restart (``docker compose down``/``up``, container recreation,
plain restart) silently lost them.

The fix: secrets are persisted as Fernet ciphertext inside admin.json, keyed
by a STABLE master key (operator env value or a once-generated secret.key on
the same volume). These tests pin every property of that contract:

- round-trip across store instances (restart simulation)
- ciphertext (never plaintext) on disk
- stable key file: not regenerated across instances
- wrong/missing master key → data PRESERVED + reported unreadable + env
  fallback; never deleted, never overwritten, never a new key
- legacy plaintext file migrated (adopted + scrubbed)
- env key override wins and is stable
- API level: save → recreate app → key still "set" from "console"
"""

from __future__ import annotations

import json
from pathlib import Path

from app.admin.store import AdminSettingsStore
from app.main import create_app
from fastapi.testclient import TestClient
from tests.admin.test_admin import MUTATING, login, make_settings


def _read_admin_json(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "admin.json").read_text())


def _ciphertext_for(tmp_path: Path, field: str) -> str:
    return _read_admin_json(tmp_path)["secrets_encrypted"][field]["data"]


# ---------------------------------------------------------------------------
# Store level — restart simulation via a second store instance
# ---------------------------------------------------------------------------


class TestEncryptedRoundTrip:
    def test_secret_survives_new_store_instance(self, tmp_path: Path) -> None:
        """The D-098 regression: a NEW process (fresh store) must load the
        console key that the old one saved."""
        path = tmp_path / "admin.json"
        first = AdminSettingsStore(str(path))
        assert first.secrets_persisted
        first.save({"llm_model": "m1"}, {"llm_api_key": "sk-round-trip"})

        second = AdminSettingsStore(str(path))
        loaded = second.load()
        assert loaded["secrets"]["llm_api_key"] == "sk-round-trip"
        assert loaded["secrets_unreadable"] == []

    def test_disk_contains_ciphertext_not_plaintext(self, tmp_path: Path) -> None:
        path = tmp_path / "admin.json"
        store = AdminSettingsStore(str(path))
        store.save({}, {"llm_api_key": "sk-never-plaintext"})
        raw = path.read_text()
        assert "sk-never-plaintext" not in raw
        stored = _read_admin_json(tmp_path)
        assert stored["secrets"] == {}
        token = stored["secrets_encrypted"]["llm_api_key"]["data"]
        assert token and "sk-never-plaintext" not in token

    def test_key_file_created_once_and_stable(self, tmp_path: Path) -> None:
        """The key file must NEVER be regenerated: a regenerated key would
        orphan the stored ciphertext (the original 'lost API key' failure
        mode, just moved one level down)."""
        path = tmp_path / "admin.json"
        AdminSettingsStore(str(path)).save({}, {"llm_api_key": "sk-stable"})
        key_file = tmp_path / "secret.key"
        assert key_file.is_file()
        first_key = key_file.read_bytes()
        assert (key_file.stat().st_mode & 0o777) == 0o600

        # Second instance (restart): same key file, same key material.
        AdminSettingsStore(str(path)).load()
        assert key_file.read_bytes() == first_key

    def test_settings_and_secrets_persist_together(self, tmp_path: Path) -> None:
        path = tmp_path / "admin.json"
        AdminSettingsStore(str(path)).save({"llm_model": "kept-model"}, {"llm_api_key": "sk-both"})
        loaded = AdminSettingsStore(str(path)).load()
        assert loaded["settings"]["llm_model"] == "kept-model"
        assert loaded["secrets"]["llm_api_key"] == "sk-both"


# ---------------------------------------------------------------------------
# Fail-safe: missing / changed / malformed master key
# ---------------------------------------------------------------------------


def _make_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


class TestKeyFailureModes:
    def test_wrong_env_key_preserves_data_and_reports(self, tmp_path: Path) -> None:
        """Rotated NYAYA_SECRET_KEY: ciphertext must survive untouched, the
        field is reported unreadable, and no new key file is generated."""
        path = tmp_path / "admin.json"
        original_key = _make_key()
        store = AdminSettingsStore(str(path), secret_env_key=original_key)
        store.save({}, {"llm_api_key": "sk-rotated-away"})
        token_before = _ciphertext_for(tmp_path, "llm_api_key")

        rotated = AdminSettingsStore(str(path), secret_env_key=_make_key())
        loaded = rotated.load()
        # Stored data preserved verbatim.
        assert _ciphertext_for(tmp_path, "llm_api_key") == token_before
        # Reported, not silently dropped.
        assert loaded["secrets_unreadable"] == ["llm_api_key"]
        assert "llm_api_key" not in loaded["secrets"]
        # No secret.key generated alongside the env key path.
        assert not (tmp_path / "secret.key").exists()

    def test_save_with_unreadable_secrets_preserves_ciphertext(self, tmp_path: Path) -> None:
        """A settings save while the master key is wrong must NOT overwrite
        the stored ciphertext (the operator can still recover it by
        restoring the key)."""
        path = tmp_path / "admin.json"
        real_key = _make_key()
        AdminSettingsStore(str(path), secret_env_key=real_key).save(
            {}, {"llm_api_key": "sk-recoverable"}
        )
        token_before = _ciphertext_for(tmp_path, "llm_api_key")

        rotated = AdminSettingsStore(str(path), secret_env_key=_make_key())
        rotated.load()
        # Admin changes an unrelated setting and saves.
        rotated.save({"llm_timeout_seconds": 120}, {})
        assert _ciphertext_for(tmp_path, "llm_api_key") == token_before
        assert _read_admin_json(tmp_path)["settings"]["llm_timeout_seconds"] == 120

    def test_deleted_key_file_preserves_ciphertext(self, tmp_path: Path) -> None:
        path = tmp_path / "admin.json"
        AdminSettingsStore(str(path)).save({}, {"llm_api_key": "sk-orphaned"})
        token_before = _ciphertext_for(tmp_path, "llm_api_key")
        (tmp_path / "secret.key").unlink()

        # A fresh store generates a NEW key — but must NOT touch the stored
        # ciphertext, must not crash, and must report the field unreadable.
        store = AdminSettingsStore(str(path))
        loaded = store.load()
        assert _ciphertext_for(tmp_path, "llm_api_key") == token_before
        assert loaded["secrets_unreadable"] == ["llm_api_key"]

    def test_invalid_env_key_disables_persistence_without_destruction(self, tmp_path: Path) -> None:
        path = tmp_path / "admin.json"
        # First save with a real key so there is data to protect.
        real = AdminSettingsStore(str(path), secret_env_key=_make_key())
        real.save({}, {"llm_api_key": "sk-protected"})
        token_before = _ciphertext_for(tmp_path, "llm_api_key")

        bad = AdminSettingsStore(str(path), secret_env_key="not-a-fernet-key")
        assert not bad.secrets_persisted
        loaded = bad.load()
        # The stored ciphertext is untouched; nothing crashed.
        assert _ciphertext_for(tmp_path, "llm_api_key") == token_before
        assert loaded["secrets_unreadable"] == []
        # A save with persistence disabled must NOT wipe the file's secrets
        # section (memory-only semantics, disk preserved for key restore).
        bad.save({"llm_model": "still-here"}, {})
        assert _ciphertext_for(tmp_path, "llm_api_key") == token_before
        assert _read_admin_json(tmp_path)["settings"]["llm_model"] == "still-here"


# ---------------------------------------------------------------------------
# Legacy plaintext migration
# ---------------------------------------------------------------------------


class TestLegacyMigration:
    def test_plaintext_file_migrated_to_encrypted(self, tmp_path: Path) -> None:
        path = tmp_path / "admin.json"
        path.write_text(
            json.dumps(
                {
                    "settings": {"llm_model": "m"},
                    "secrets": {"llm_api_key": "sk-legacy-plain"},
                    "corpus": {},
                }
            )
        )
        store = AdminSettingsStore(str(path))
        loaded = store.load()
        assert loaded["secrets"]["llm_api_key"] == "sk-legacy-plain"
        # Scrubbed: no plaintext anywhere in the raw file.
        assert "sk-legacy-plain" not in path.read_text()
        stored = _read_admin_json(tmp_path)
        assert stored["secrets"] == {}
        assert "llm_api_key" in stored["secrets_encrypted"]

    def test_migration_without_box_leaves_file_untouched(self, tmp_path: Path) -> None:
        """When encryption is unavailable, adopting-and-scrubbing would
        destroy the only copy: the file must be left exactly as it was."""
        path = tmp_path / "admin.json"
        legacy = json.dumps(
            {"settings": {}, "secrets": {"llm_api_key": "sk-only-copy"}, "corpus": {}}
        )
        path.write_text(legacy)
        store = AdminSettingsStore(str(path), secret_env_key="not-a-fernet-key")
        loaded = store.load()
        assert loaded["secrets"]["llm_api_key"] == "sk-only-copy"
        assert path.read_text() == legacy


# ---------------------------------------------------------------------------
# Env master key semantics
# ---------------------------------------------------------------------------


class TestEnvMasterKey:
    def test_env_key_wins_and_is_stable(self, tmp_path: Path) -> None:
        path = tmp_path / "admin.json"
        key = _make_key()
        AdminSettingsStore(str(path), secret_env_key=key).save({}, {"llm_api_key": "sk-env-keyed"})
        assert not (tmp_path / "secret.key").exists()
        again = AdminSettingsStore(str(path), secret_env_key=key)
        assert again.load()["secrets"]["llm_api_key"] == "sk-env-keyed"

    def test_env_key_used_across_instances_round_trip(self, tmp_path: Path) -> None:
        """Key provided by the operator: BOTH instances must use the same key
        (the container-recreation case for env-configured deployments)."""
        path = tmp_path / "admin.json"
        key = _make_key()
        AdminSettingsStore(str(path), secret_env_key=key).save(
            {"rate_limit_chat_per_minute": 5}, {"llm_api_key": "sk-across"}
        )
        # Simulated new process: same env var, same file.
        second = AdminSettingsStore(str(path), secret_env_key=key)
        loaded = second.load()
        assert loaded["secrets"]["llm_api_key"] == "sk-across"
        assert loaded["secrets_unreadable"] == []


# ---------------------------------------------------------------------------
# Clear semantics on disk
# ---------------------------------------------------------------------------


class TestClearRemovesCiphertext:
    def test_cleared_secret_removed_from_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "admin.json"
        store = AdminSettingsStore(str(path))
        store.save({}, {"llm_api_key": "sk-gone"})
        assert "llm_api_key" in _read_admin_json(tmp_path)["secrets_encrypted"]
        # save() with the field absent = cleared (handler merges before save).
        store.save({}, {})
        stored = _read_admin_json(tmp_path)
        assert "llm_api_key" not in stored["secrets_encrypted"]


# ---------------------------------------------------------------------------
# API level — the operator-visible lifecycle
# ---------------------------------------------------------------------------


class TestApiPersistenceLifecycle:
    def test_saved_key_survives_app_recreation(self, tmp_path: Path) -> None:
        """PUT a key → tear the app down → build a NEW app from the same
        settings file: the key must still be set, sourced from "console"."""
        settings = make_settings(tmp_path)
        with TestClient(create_app(settings=settings)) as client:
            login(client)
            response = client.put(
                "/api/v1/admin/settings",
                # force: persistence (not verification) is under test here.
                json={"values": {}, "secrets": {"llm_api_key": "sk-across-restart"}, "force": True},
                headers=MUTATING,
            )
            assert response.status_code == 200
            assert response.json()["secret_sources"]["llm_api_key"] == "console"
            assert response.json()["secrets_persisted"] is True

        # "docker compose down && up" for the app tier: a fresh process built
        # from the same environment + the same persistent volume.
        with TestClient(create_app(settings=make_settings(tmp_path))) as client:
            login(client)
            body = client.get("/api/v1/admin/settings").json()
            assert body["secrets"]["llm_api_key"] == "set"
            assert body["secret_sources"]["llm_api_key"] == "console"
            assert body["secrets_unreadable"] == []
            # No plaintext secret anywhere in the persisted file.
            assert "sk-across-restart" not in (tmp_path / "admin.json").read_text()

    def test_unreadable_reported_through_api(self, tmp_path: Path) -> None:
        """Rotated key: the API must surface the configuration error instead
        of silently reporting the key gone."""
        key = _make_key()
        settings = make_settings(tmp_path, secrets_master_key=key)
        with TestClient(create_app(settings=settings)) as client:
            login(client)
            client.put(
                "/api/v1/admin/settings",
                json={"values": {}, "secrets": {"llm_api_key": "sk-lockbox"}, "force": True},
                headers=MUTATING,
            )

        # Operator rotates NYAYA_SECRET_KEY, container is recreated.
        rotated_settings = make_settings(tmp_path, secrets_master_key=_make_key())
        with TestClient(create_app(settings=rotated_settings)) as client:
            login(client)
            body = client.get("/api/v1/admin/settings").json()
            assert body["secrets_unreadable"] == ["llm_api_key"]
            assert body["secrets"]["llm_api_key"] == ""  # not silently "set"
            # The stored ciphertext is still on disk, untouched.
            assert "llm_api_key" in _read_admin_json(tmp_path)["secrets_encrypted"]

    def test_env_key_unset_uses_volume_key_file(self, tmp_path: Path) -> None:
        """Default deployment (no NYAYA_SECRET_KEY): the key file on the same
        volume as admin.json provides stability across recreation."""
        settings = make_settings(tmp_path)  # no secrets_master_key
        with TestClient(create_app(settings=settings)) as client:
            login(client)
            response = client.put(
                "/api/v1/admin/settings",
                json={"values": {}, "secrets": {"llm_api_key": "sk-volume-key"}, "force": True},
                headers=MUTATING,
            )
            assert response.status_code == 200
            assert response.json()["secrets_persisted"] is True
        assert (tmp_path / "secret.key").is_file()

        with TestClient(create_app(settings=make_settings(tmp_path))) as client:
            login(client)
            body = client.get("/api/v1/admin/settings").json()
            assert body["secrets"]["llm_api_key"] == "set"
            assert body["secrets_persisted"] is True
