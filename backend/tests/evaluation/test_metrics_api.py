"""Metrics endpoint and instrumentation tests (F-025..F-036)."""

from __future__ import annotations

from app.core.config import Settings
from app.main import create_app
from app.observability.metrics import MetricsRegistry
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.generation.fixtures import ScriptedProvider


def _counter_value(body: str, metric: str, labels: str = "") -> float:
    """Parse one counter series value out of the rendered text."""
    import re

    pattern = re.escape(f"{metric}{labels}")
    match = re.search(rf"^{pattern} (\S+)$", body, re.MULTILINE)
    if match is None:
        # Metric registered but no observations yet: value is zero.
        assert f"# TYPE {metric}" in body, f"{metric} not found"
        return 0.0
    return float(match.group(1))


def _app(provider: ScriptedProvider) -> FastAPI:
    settings = Settings(_env_file=None)
    app = create_app(settings=settings)
    app.state.llm_registry.register("stub", lambda _s: provider)
    app.state.settings = settings.model_copy(
        update={
            "llm_provider": "stub",
            "llm_cost_per_1k_input_tokens": 0.5,
            "llm_cost_per_1k_output_tokens": 1.0,
        }
    )
    return app


def test_render_prometheus_text_format() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("nyaya_test_total", "test counter", ("kind",))
    gauge = registry.gauge("nyaya_test_up", "test gauge")
    histogram = registry.histogram("nyaya_test_seconds", "test histogram")
    counter.inc(2, kind="a")
    gauge.set(1)
    histogram.observe(0.3)
    text = registry.render()
    assert "# TYPE nyaya_test_total counter" in text
    assert 'nyaya_test_total{kind="a"} 2' in text
    assert "nyaya_test_up 1" in text
    assert "# TYPE nyaya_test_seconds histogram" in text
    assert "nyaya_test_seconds_count 1" in text
    assert 'nyaya_test_seconds_bucket{le="0.5"} 1' in text


def test_render_escapes_label_values() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("nyaya_test_total", "t", ("path",))
    counter.inc(path='/quo"te\\')
    assert 'path="/quo\\"te\\\\"' in registry.render()


def test_metric_label_validation() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("nyaya_test_total", "t", ("kind",))
    try:
        counter.inc(wrong="x")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_metrics_endpoint_exposes_application_metrics() -> None:
    client = TestClient(_app(ScriptedProvider(["answer [TS s.103]"] * 4)))
    response = client.get("/api/v1/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    for name in (
        "nyaya_requests_total",
        "nyaya_request_latency_seconds",
        "nyaya_embedding_latency_seconds",
        "nyaya_retrieval_latency_seconds",
        "nyaya_vector_db_up",
        "nyaya_tokens_total",
        "nyaya_uploads_total",
        "nyaya_refusals_total",
        "nyaya_estimated_query_cost_total",
    ):
        assert f"# HELP {name}" in body, name


def test_request_counter_records_route_and_status() -> None:
    app = _app(ScriptedProvider(["ok"] * 4))
    client = TestClient(app)
    client.get("/api/v1/health")
    client.get("/api/v1/nonexistent")
    body = client.get("/api/v1/metrics").text
    assert 'nyaya_requests_total{method="GET",route="/api/v1/health",status="200"}' in body
    assert 'nyaya_requests_total{method="GET",route="/api/v1/nonexistent",status="404"}' in body


def test_refusal_counter_increments_on_refusal() -> None:
    # A provider that never runs: the confidence gate refuses first.
    app = _app(ScriptedProvider(["should not be used"] * 4))
    app.state.retrieval_service = _refusing_service()
    client = TestClient(app)
    before = _counter_value(client.get("/api/v1/metrics").text, "nyaya_refusals_total")
    client.post("/api/v1/chat", json={"message": "unanswerable", "history": []})
    after = _counter_value(client.get("/api/v1/metrics").text, "nyaya_refusals_total")
    assert after == before + 1


def _refusing_service() -> object:
    from app.retrieval.service import RetrievalService
    from app.retrieval.sparse import Bm25SparseIndex
    from app.retrieval.store import ChunkStore
    from tests.retrieval.fixtures import FakeDenseRetriever, make_corpus

    chunks = make_corpus()
    # No dense hits and a threshold of 1.0: retrieval is never sufficient.
    return RetrievalService(
        ChunkStore(chunks),
        FakeDenseRetriever({}),
        Bm25SparseIndex(chunks),
        confidence_threshold=1.0,
    )


def test_token_and_cost_metrics_recorded_after_chat() -> None:
    provider = _TokenCountingProvider()
    app = _app(provider)
    from app.retrieval.service import RetrievalService
    from app.retrieval.sparse import Bm25SparseIndex
    from app.retrieval.store import ChunkStore
    from tests.retrieval.fixtures import FakeDenseRetriever, make_corpus

    chunks = make_corpus()
    app.state.retrieval_service = RetrievalService(
        ChunkStore(chunks),
        FakeDenseRetriever({"anything": ["ts-s103-001"]}),
        Bm25SparseIndex(chunks),
        confidence_threshold=0.0,
    )
    client = TestClient(app)
    client.post("/api/v1/chat", json={"message": "What is murder?", "history": []})
    body = client.get("/api/v1/metrics").text
    assert 'nyaya_tokens_total{kind="input"}' in body
    assert 'nyaya_tokens_total{kind="output"}' in body
    assert "# HELP nyaya_estimated_query_cost_total" in body
    assert "# HELP nyaya_last_query_cost_estimate" in body


def test_vector_db_up_reflects_components() -> None:
    app = _app(ScriptedProvider(["ok"] * 4))
    client = TestClient(app)
    body = client.get("/api/v1/metrics").text
    # Document subsystem is always built; statute requires a corpus config.
    assert 'nyaya_vector_db_up{component="documents"} 1' in body
    assert 'nyaya_vector_db_up{component="statute"}' in body


class _TokenCountingProvider(ScriptedProvider):
    """ScriptedProvider variant that reports token usage (F-030)."""

    def __init__(self) -> None:
        super().__init__(["Murder is punishable [TS s.103]."] * 4)

    async def generate(self, request: object) -> object:  # type: ignore[override]
        from app.llm.base import GenerationResult

        return GenerationResult(
            text="Murder is punishable [TS s.103].",
            model="stub",
            prompt_tokens=100,
            completion_tokens=50,
        )
