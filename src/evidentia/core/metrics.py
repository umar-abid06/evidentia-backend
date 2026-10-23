"""Prometheus metrics: HTTP traffic and every model call, by stage and model."""

from __future__ import annotations

import os

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    make_asgi_app,
    multiprocess,
)
from starlette.types import ASGIApp

from evidentia.ai.telemetry import LLMCallRecord

HTTP_REQUESTS = Counter(
    "evidentia_http_requests_total", "HTTP requests.", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "evidentia_http_request_duration_seconds",
    "HTTP request latency.",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
LLM_CALLS = Counter("evidentia_llm_calls_total", "Model calls.", ["stage", "model", "outcome"])
LLM_TOKENS = Counter("evidentia_llm_tokens_total", "Model tokens.", ["stage", "model", "kind"])
LLM_RETRIES = Counter("evidentia_llm_retries_total", "Model call retries.", ["stage", "model"])
LLM_LATENCY = Histogram(
    "evidentia_llm_call_duration_seconds",
    "Model call latency, including retries.",
    ["stage", "model"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64, 128),
)


def observe_http(method: str, route: str, status: int, seconds: float) -> None:
    HTTP_REQUESTS.labels(method, route, str(status)).inc()
    HTTP_LATENCY.labels(method, route).observe(seconds)


def prometheus_sink(record: LLMCallRecord) -> None:
    """Telemetry sink: mirrors each recorded model call into Prometheus."""
    outcome = "success" if record.success else (record.error_type or "error")
    LLM_CALLS.labels(record.stage, record.model, outcome).inc()
    LLM_TOKENS.labels(record.stage, record.model, "prompt").inc(record.prompt_tokens)
    LLM_TOKENS.labels(record.stage, record.model, "completion").inc(record.completion_tokens)
    if record.retries:
        LLM_RETRIES.labels(record.stage, record.model).inc(record.retries)
    LLM_LATENCY.labels(record.stage, record.model).observe(record.latency_ms / 1000)


def metrics_app() -> ASGIApp:
    """Exposition endpoint; aggregates across processes when multiprocess mode is on."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        app: ASGIApp = make_asgi_app(registry=registry)
        return app
    default: ASGIApp = make_asgi_app()
    return default
