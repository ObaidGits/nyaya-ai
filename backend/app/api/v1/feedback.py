"""Feedback endpoint (REQUIREMENTS.md D-025..D-027; PRD §15.2).

Stores thumbs up/down votes with an optional free-text comment against the
session that submitted them. Persistence is in-process by design: feedback
is telemetry, not legal data, and the anonymous-session model (D-041) has no
account to attach votes to. The storage seam is a single class so a later
phase can swap in the PostgreSQL sink without touching the endpoint.
"""

from __future__ import annotations

import threading
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from app.api.v1.documents import get_session_id
from app.core.rate_limit import FEEDBACK_SCOPE, enforce_rate_limit
from app.domain.models import FeedbackVote

router = APIRouter(tags=["feedback"])

#: Loose budget: feedback is telemetry, but it must still be bounded so an
#: anonymous client cannot append unbounded rows per minute.
FEEDBACK_MAX_PER_MINUTE = 30


class FeedbackRequest(BaseModel):
    """Feedback payload: a vote plus an optional comment."""

    vote: FeedbackVote
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackResponse(BaseModel):
    """Acknowledgement returned after a vote is recorded."""

    status: str
    vote: FeedbackVote


class FeedbackStore:
    """Thread-safe in-memory feedback sink (swap point for a DB backend)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[tuple[str, FeedbackVote, str | None]] = []

    def record(self, session_id: str, vote: FeedbackVote, comment: str | None) -> None:
        with self._lock:
            self._entries.append((session_id, vote, comment))

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def entries_for(self, session_id: str) -> list[tuple[FeedbackVote, str | None]]:
        with self._lock:
            return [(vote, comment) for sid, vote, comment in self._entries if sid == session_id]


def get_feedback_store(request: Request) -> FeedbackStore:
    """Return the app-scoped feedback store, creating it lazily."""
    store = getattr(request.app.state, "feedback_store", None)
    if store is None:
        store = FeedbackStore()
        request.app.state.feedback_store = store
    return store


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record answer feedback",
)
async def submit_feedback(
    payload: FeedbackRequest,
    raw_request: Request,
    session_id: Annotated[str, Depends(get_session_id)],
    store: Annotated[FeedbackStore, Depends(get_feedback_store)],
) -> FeedbackResponse:
    """Persist a thumbs up/down vote with an optional comment (D-026/D-027)."""
    limiter = getattr(raw_request.app.state, "rate_limiter", None)
    if limiter is not None:
        client_host = raw_request.client.host if raw_request.client else "anonymous"
        enforce_rate_limit(
            limiter,
            scope=FEEDBACK_SCOPE,
            key=client_host,
            limit=FEEDBACK_MAX_PER_MINUTE,
            window_seconds=60.0,
        )
    store.record(session_id, payload.vote, payload.comment)
    return FeedbackResponse(status="recorded", vote=payload.vote)
