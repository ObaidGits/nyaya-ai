"""Feedback endpoint tests (REQUIREMENTS.md D-025..D-027)."""

from __future__ import annotations

from fastapi.testclient import TestClient

SESSION = "feedback-session-0001"
HEADERS = {"X-Session-Id": SESSION}


def test_feedback_up_is_recorded(client: TestClient) -> None:
    response = client.post("/api/v1/feedback", json={"vote": "up"}, headers=HEADERS)
    assert response.status_code == 201
    body = response.json()
    assert body == {"status": "recorded", "vote": "up"}


def test_feedback_down_with_optional_comment(client: TestClient) -> None:
    response = client.post(
        "/api/v1/feedback",
        json={"vote": "down", "comment": "wrong section cited"},
        headers=HEADERS,
    )
    assert response.status_code == 201
    assert response.json()["vote"] == "down"


def test_feedback_requires_session(client: TestClient) -> None:
    response = client.post("/api/v1/feedback", json={"vote": "up"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SESSION_REQUIRED"


def test_feedback_rejects_invalid_vote(client: TestClient) -> None:
    response = client.post("/api/v1/feedback", json={"vote": "sideways"}, headers=HEADERS)
    assert response.status_code == 422


def test_feedback_rejects_overlong_comment(client: TestClient) -> None:
    response = client.post(
        "/api/v1/feedback",
        json={"vote": "up", "comment": "x" * 2001},
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_feedback_store_is_session_scoped(client: TestClient) -> None:
    from app.api.v1.feedback import FeedbackStore

    store = FeedbackStore()
    store.record(SESSION, "up", "good")  # type: ignore[arg-type]
    store.record("other-session-0002", "down", None)  # type: ignore[arg-type]
    assert store.count() == 2
    assert store.entries_for(SESSION) == [("up", "good")]
