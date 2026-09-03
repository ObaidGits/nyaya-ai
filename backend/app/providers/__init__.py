"""Provider pool package: configurable multi-provider failover (2026-09).

Shared, provider-agnostic building blocks so LLM, STT and TTS pools use one
abstraction (no scattered provider-specific conditionals):

- ``models``    — pool/entry configuration (persisted by the admin store)
- ``health``    — per-entry circuit state (cooldown after failures)
- ``router``    — the bounded failover engine itself
"""
