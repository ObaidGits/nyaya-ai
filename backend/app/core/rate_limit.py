"""In-memory per-session rate limiting (REQUIREMENTS D-049/D-050; ARCHITECTURE §48).

A sliding-window counter keyed by session identity. Two independent budgets:
chat requests and document uploads. Exceeding a budget raises a 429 with the
standard error envelope. The limiter is process-local state attached to the
application (single-process deployment; no shared store is in scope).
"""

from __future__ import annotations

import threading
import time
from collections import deque

from app.core.errors import AppError

CHAT_SCOPE = "chat"
UPLOAD_SCOPE = "upload"
SPEECH_SCOPE = "speech"


class RateLimitExceededError(AppError):
    """The session has exceeded its request budget (D-049/D-050)."""

    status_code = 429
    code = "RATE_LIMITED"


class RateLimiter:
    """Thread-safe sliding-window limiter for named scopes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[tuple[str, str], deque[float]] = {}

    def allow(self, scope: str, key: str, limit: int, window_seconds: float) -> bool:
        """Record one event and report whether it was within budget."""
        now = time.monotonic()
        with self._lock:
            events = self._events.setdefault((scope, key), deque())
            cutoff = now - window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True

    def reset(self) -> None:
        """Drop all recorded events (test seam)."""
        with self._lock:
            self._events.clear()


def enforce_rate_limit(
    limiter: RateLimiter, *, scope: str, key: str, limit: int, window_seconds: float
) -> None:
    """Raise the 429 application error when the budget is exhausted."""
    if not limiter.allow(scope, key, limit, window_seconds):
        raise RateLimitExceededError(
            "Too many requests. Please wait a moment and try again.",
        )
