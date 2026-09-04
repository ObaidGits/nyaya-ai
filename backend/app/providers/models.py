"""Pool configuration models (persisted; no secrets — those live encrypted).

A pool is an ordered list of provider entries plus routing metadata. The
runtime router turns a pool into a failover chain; an empty pool means
"environment single-provider mode" (the pre-pool behavior, unchanged).
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class FailoverStrategy(StrEnum):
    """How the next provider is selected when the current one fails."""

    #: Try the default entry first, then ascending priority. Deterministic
    #: and predictable — the default for legal answers where consistency
    #: of the primary provider matters.
    PRIORITY = "priority"
    #: Rotate the starting entry per request (load spreading); on failure
    #: continue the rotation. Still respects enable/disable and cooldowns.
    ROUND_ROBIN = "round_robin"


_ENTRY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ProviderEntryConfig(BaseModel):
    """One configured provider inside a pool."""

    #: Stable identifier (used as the encrypted-secret key suffix and in
    #: health reports). Slug-shaped so it is safe in JSON keys and URLs.
    id: str
    #: Registry name: "groq", "gemini", "ollama", "openai", ... (LLM) or
    #: "whisper", "faster-whisper", "browser", "openai", ... (speech).
    provider: str
    label: str = ""
    model: str = ""
    base_url: str = ""
    enabled: bool = True
    #: Lower value = tried earlier within the strategy's rotation.
    priority: int = Field(default=100, ge=0, le=10_000)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _ENTRY_ID_RE.fullmatch(value):
            raise ValueError("entry id must be a 1-64 char slug of a-z, 0-9 and '-'")
        return value

    @field_validator("provider")
    @classmethod
    def _valid_provider(cls, value: str) -> str:
        if not value or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,40}", value):
            raise ValueError("provider must be a lowercase registry name")
        return value


class ProviderPoolConfig(BaseModel):
    """A pool: ordered entries + routing metadata for one capability."""

    entries: list[ProviderEntryConfig] = Field(default_factory=list)
    #: The entry tried first (the "preferred" provider). Must reference an
    #: enabled entry; None means "highest-priority enabled entry".
    default_entry_id: str | None = None
    strategy: FailoverStrategy = FailoverStrategy.PRIORITY

    def entry(self, entry_id: str) -> ProviderEntryConfig | None:
        for candidate in self.entries:
            if candidate.id == entry_id:
                return candidate
        return None

    def enabled_entries(self) -> list[ProviderEntryConfig]:
        return [entry for entry in self.entries if entry.enabled]

    def ordered_entries(self, rotation: int = 0) -> list[ProviderEntryConfig]:
        """Entries in failover order, honoring default + strategy.

        ``rotation`` is the round-robin rotation counter (requests served);
        it is ignored under the PRIORITY strategy.
        """
        enabled = self.enabled_entries()
        by_priority = sorted(enabled, key=lambda e: (e.priority, e.id))
        if self.default_entry_id:
            default = [e for e in by_priority if e.id == self.default_entry_id]
            rest = [e for e in by_priority if e.id != self.default_entry_id]
            by_priority = default + rest
        if self.strategy == FailoverStrategy.ROUND_ROBIN and by_priority:
            rotation %= len(by_priority)
            by_priority = by_priority[rotation:] + by_priority[:rotation]
        return by_priority

    def validate_default(self) -> None:
        """The default entry must exist and be enabled (runtime honesty)."""
        if self.default_entry_id is None:
            return
        entry = self.entry(self.default_entry_id)
        if entry is None:
            raise ValueError(f"default entry '{self.default_entry_id}' is not in the pool")
        if not entry.enabled:
            raise ValueError(f"default entry '{self.default_entry_id}' is disabled")


class PoolSecrets(BaseModel):
    """API keys per (pool, entry) — plaintext only in memory, never
    persisted unencrypted and never echoed to the client."""

    llm: dict[str, str] = Field(default_factory=dict)
    stt: dict[str, str] = Field(default_factory=dict)
    tts: dict[str, str] = Field(default_factory=dict)

    def get(self, pool: str, entry_id: str) -> str:
        bucket: dict[str, str] = getattr(self, pool)
        return bucket.get(entry_id, "")

    def set(self, pool: str, entry_id: str, value: str) -> None:
        if not value:
            getattr(self, pool, {}).pop(entry_id, None)
            return
        getattr(self, pool)[entry_id] = value


#: Secret keys in the settings file: "pool:<pool>:<entry_id>".
POOL_SECRET_PREFIX = "pool:"


def pool_secret_key(pool: str, entry_id: str) -> str:
    return f"{POOL_SECRET_PREFIX}{pool}:{entry_id}"
