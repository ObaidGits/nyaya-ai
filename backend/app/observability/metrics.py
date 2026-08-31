"""Prometheus-compatible metrics registry (ARCHITECTURE §41, F-025..F-036).

Minimal, dependency-free exposition of counters, gauges and histograms in
the Prometheus text format. The registry is process-global: handlers import
it and record measurements inline; ``GET /api/v1/metrics`` renders it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from typing import cast

# Latency buckets covering embedding (sub-ms) through generation (minutes).
_DEFAULT_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
)

LabelValues = tuple[str, ...]


class _Metric:
    """Base type carrying a name, help text and label dimensions."""

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...]) -> None:
        self.name = name
        self.help = help_text
        self.label_names = label_names
        self.series: dict[LabelValues, float | dict[str, float]] = {}

    def _key(self, labels: dict[str, str]) -> LabelValues:
        if set(labels) != set(self.label_names):
            raise ValueError(
                f"metric {self.name} expects labels {self.label_names}, got {sorted(labels)}"
            )
        return tuple(labels[name] for name in self.label_names)


class Counter(_Metric):
    def inc(self, value: float = 1.0, **labels: str) -> None:
        key = self._key(labels)
        current = self.series.get(key, 0.0)
        assert isinstance(current, float)
        self.series[key] = current + value


class Gauge(_Metric):
    def set(self, value: float, **labels: str) -> None:
        self.series[self._key(labels)] = float(value)


class Histogram(_Metric):
    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...] = (),
        buckets: Iterable[float] = _DEFAULT_BUCKETS,
    ) -> None:
        super().__init__(name, help_text, label_names)
        self.buckets = tuple(sorted(set(buckets)))

    def observe(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        series = self.series.get(key)
        if series is None:
            series = {"count": 0.0, "sum": 0.0, **{f"b{b}": 0.0 for b in self.buckets}}
            self.series[key] = series
        assert isinstance(series, dict)
        series["count"] += 1.0
        series["sum"] += value
        for bucket in self.buckets:
            if value <= bucket:
                series[f"b{bucket}"] += 1.0

    def observe_duration(self, **labels: str) -> _Timer:
        return _Timer(self, labels)


class _Timer:
    """Context manager recording wall-clock duration into a histogram."""

    def __init__(self, histogram: Histogram, labels: dict[str, str]) -> None:
        self._histogram = histogram
        self._labels = labels
        self._start = 0.0

    def __enter__(self) -> _Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._histogram.observe(time.perf_counter() - self._start, **self._labels)


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_labels(label_names: tuple[str, ...], values: LabelValues) -> str:
    if not label_names:
        return ""
    pairs = ",".join(
        f'{name}="{_escape_label_value(value)}"'
        for name, value in zip(label_names, values, strict=True)
    )
    return "{" + pairs + "}"


class MetricsRegistry:
    """Thread-safe collection of metrics with Prometheus text exposition."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: dict[str, _Metric] = {}

    def counter(self, name: str, help_text: str, label_names: tuple[str, ...] = ()) -> Counter:
        return cast(Counter, self._register(Counter(name, help_text, label_names)))

    def gauge(self, name: str, help_text: str, label_names: tuple[str, ...] = ()) -> Gauge:
        return cast(Gauge, self._register(Gauge(name, help_text, label_names)))

    def histogram(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...] = (),
        buckets: Iterable[float] = _DEFAULT_BUCKETS,
    ) -> Histogram:
        return cast(Histogram, self._register(Histogram(name, help_text, label_names, buckets)))

    def _register(self, metric: _Metric) -> object:
        # Registering the same name twice returns the first metric, so every
        # importer shares one series instead of resetting it.
        with self._lock:
            existing = self._metrics.get(metric.name)
            if existing is not None:
                return existing
            self._metrics[metric.name] = metric
            return metric

    def render(self) -> str:
        """Render all series in the Prometheus text exposition format."""
        lines: list[str] = []
        with self._lock:
            for name in sorted(self._metrics):
                metric = self._metrics[name]
                lines.append(f"# HELP {name} {metric.help}")
                if isinstance(metric, Histogram):
                    lines.append(f"# TYPE {name} histogram")
                    for values in sorted(metric.series):
                        series = metric.series[values]
                        assert isinstance(series, dict)
                        label = _format_labels(metric.label_names, values)
                        for bucket in metric.buckets:
                            # Prometheus wants every label inside one pair of
                            # braces, and label values must be QUOTED strings
                            # (le="0.005", not le=0.005 — the text format
                            # rejects unquoted values). Render bounds via %g
                            # so 0.005 stays "0.005" and 60.0 stays "60".
                            bound = f"{bucket:g}"
                            bucket_labels = (
                                f'{{le="{bound}"}}' if not label else f'{label[:-1]},le="{bound}"}}'
                            )
                            lines.append(f"{name}_bucket{bucket_labels} {series[f'b{bucket}']:g}")
                        lines.append(
                            f'{name}_bucket{label[:-1]},le="+Inf"}} {series["count"]:g}'
                            if label
                            else f'{name}_bucket{{le="+Inf"}} {series["count"]:g}'
                        )
                        lines.append(f"{name}_count{label} {series['count']:g}")
                        lines.append(f"{name}_sum{label} {series['sum']:g}")
                elif isinstance(metric, Gauge):
                    lines.append(f"# TYPE {name} gauge")
                    for values in sorted(metric.series):
                        value = metric.series[values]
                        assert isinstance(value, float)
                        lines.append(
                            f"{name}{_format_labels(metric.label_names, values)} {value:g}"
                        )
                else:
                    lines.append(f"# TYPE {name} counter")
                    for values in sorted(metric.series):
                        value = metric.series[values]
                        assert isinstance(value, float)
                        lines.append(
                            f"{name}{_format_labels(metric.label_names, values)} {value:g}"
                        )
        return "\n".join(lines) + "\n"


REGISTRY = MetricsRegistry()

# --- Application metrics (F-025..F-034) ------------------------------------

REQUESTS = REGISTRY.counter(
    "nyaya_requests_total",
    "Total HTTP requests by method, route and status.",
    ("method", "route", "status"),
)
REQUEST_LATENCY = REGISTRY.histogram(
    "nyaya_request_latency_seconds",
    "End-to-end HTTP request latency in seconds.",
    ("method", "route"),
)
EMBEDDING_LATENCY = REGISTRY.histogram(
    "nyaya_embedding_latency_seconds", "Query embedding time in seconds."
)
RETRIEVAL_LATENCY = REGISTRY.histogram(
    "nyaya_retrieval_latency_seconds", "Retrieval pipeline latency in seconds.", ("route",)
)
VECTOR_DB_UP = REGISTRY.gauge(
    "nyaya_vector_db_up", "Indexed corpus availability by component (1=up, 0=down).", ("component",)
)
TOKENS = REGISTRY.counter(
    "nyaya_tokens_total", "LLM tokens consumed by kind (input/output).", ("kind",)
)
UPLOADS = REGISTRY.counter("nyaya_uploads_total", "User documents accepted for ingestion.")
REFUSALS = REGISTRY.counter("nyaya_refusals_total", "Answers refused by the confidence gate.")
ESTIMATED_COST = REGISTRY.counter(
    "nyaya_estimated_query_cost_total", "Estimated query cost in USD (tokens x provider rate)."
)
LAST_QUERY_COST = REGISTRY.gauge(
    "nyaya_last_query_cost_estimate", "Estimated cost of the most recent query in USD."
)


def seed_default_series() -> None:
    """Pre-create the zero series for headless counters/gauges (F-033).

    A restarted process has empty counters until the first event lands, so a
    Prometheus scrape right after boot reports nothing and dashboards look
    like the metric vanished. Seeding the expected label combinations at
    startup keeps the series continuously present at 0.
    """
    TOKENS.inc(0.0, kind="input")
    TOKENS.inc(0.0, kind="output")
    UPLOADS.inc(0.0)
    REFUSALS.inc(0.0)
    ESTIMATED_COST.inc(0.0)
    LAST_QUERY_COST.set(0.0)
