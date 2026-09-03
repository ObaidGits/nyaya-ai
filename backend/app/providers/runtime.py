"""Central provider-pool runtime wiring (startup + admin-save rebuild).

One function, ``rebuild_pool_runtime``, is the ONLY place that turns
persisted pool configs into live failover wrappers, and it is called at
startup and after every admin settings/pool save. Routing consumers
(``get_llm_provider``, the speech endpoints) check the runtime and fall
back to the unchanged single-provider ENV path when a pool is empty —
so nothing about the pre-pool behavior changes until an admin actually
configures a pool.

The ``HealthBoard`` deliberately OUTLIVES rebuilds (kept on app.state):
cooldowns and circuit state survive a settings save, so saving unrelated
settings cannot reset a provider's "cooling" state mid-incident.
"""

from __future__ import annotations

import logging
from typing import Any

from app.admin.store import AdminSettingsStore
from app.core.config import Settings
from app.llm.pool import build_llm_failover_provider
from app.llm.registry import ProviderRegistry
from app.providers.health import HealthBoard
from app.providers.models import ProviderPoolConfig
from app.speech.pool import build_speech_failover

logger = logging.getLogger(__name__)

_EMPTY_POOL = ProviderPoolConfig()


class ProviderPoolRuntime:
    """The built pools for one process lifetime (until the next rebuild)."""

    def __init__(
        self,
        board: HealthBoard,
        configs: dict[str, ProviderPoolConfig],
        llm: Any | None,
        stt: Any | None,
        tts: Any | None,
    ) -> None:
        self.board = board
        self.configs = configs
        self.llm = llm
        self.stt = stt
        self.tts = tts

    @property
    def active(self) -> bool:
        """True when any pool has a usable entry (ENV fallback otherwise)."""
        return self.llm is not None or self.stt is not None or self.tts is not None


def rebuild_pool_runtime(
    app_state: Any,
    store: AdminSettingsStore,
    settings: Settings,
    registry: ProviderRegistry,
) -> ProviderPoolRuntime:
    """Build/refresh the pools and rewire the speech service.

    Called at startup and from ``_apply_settings`` after admin saves. Safe
    to call repeatedly; a pool that fails to build degrades to ENV mode
    (logged), never to a crashed app.
    """
    board: HealthBoard = getattr(app_state, "provider_health_board", None) or HealthBoard()
    app_state.provider_health_board = board

    try:
        configs = store.load_pool_configs()
        secrets = store.load_pool_secrets()
    except Exception:
        logger.exception("provider pool config unreadable; using ENV mode")
        configs, secrets = {}, None
    from app.providers.models import PoolSecrets

    if secrets is None:
        secrets = PoolSecrets()

    llm = stt = tts = None
    try:
        llm = build_llm_failover_provider(
            configs.get("llm") or _EMPTY_POOL, secrets, settings, registry, board
        )
    except Exception:
        logger.exception("llm pool build failed; using ENV single-provider mode")
    try:
        stt = build_speech_failover(
            "stt", configs.get("stt") or _EMPTY_POOL, secrets, settings, board
        )
    except Exception:
        logger.exception("stt pool build failed; using ENV single-provider mode")
    try:
        tts = build_speech_failover(
            "tts", configs.get("tts") or _EMPTY_POOL, secrets, settings, board
        )
    except Exception:
        logger.exception("tts pool build failed; using ENV single-provider mode")

    runtime = ProviderPoolRuntime(board, configs, llm, stt, tts)
    app_state.provider_pool_runtime = runtime

    # Speech service: pool wrappers override ENV construction per side; the
    # other side keeps the standard lazy ENV build. Rebuilding the service
    # matches the existing admin-save behavior (providers are lazy, so this
    # never loads model weights).
    from app.speech.service import SpeechService, create_speech_service

    if stt is None and tts is None:
        app_state.speech_service = create_speech_service(settings)
    else:
        app_state.speech_service = SpeechService(
            stt=stt, tts=tts, settings=settings
        )

    logger.info(
        "provider pools built",
        extra={
            "llm_entries": len((configs.get("llm") or _EMPTY_POOL).enabled_entries()),
            "stt_entries": len((configs.get("stt") or _EMPTY_POOL).enabled_entries()),
            "tts_entries": len((configs.get("tts") or _EMPTY_POOL).enabled_entries()),
            "llm_failover_active": llm is not None,
            "stt_failover_active": stt is not None,
            "tts_failover_active": tts is not None,
        },
    )
    return runtime
