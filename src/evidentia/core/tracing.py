"""OpenTelemetry tracing, exported over OTLP/HTTP when an endpoint is configured."""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio

from evidentia import __version__
from evidentia.core.config import ObservabilitySettings

_configured = False


def configure_tracing(config: ObservabilitySettings, *, service: str, environment: str) -> bool:
    """Installs a global tracer provider once per process. Returns True if enabled."""
    global _configured  # noqa: PLW0603 - process-wide, idempotent
    if _configured or not config.otlp_endpoint:
        return _configured
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service,
                "service.version": __version__,
                "deployment.environment": environment,
            }
        ),
        sampler=ParentBasedTraceIdRatio(config.trace_sample_ratio),
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{config.otlp_endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    _configured = True
    return True


def instrument_app(app: FastAPI, config: ObservabilitySettings, *, environment: str) -> None:
    if configure_tracing(config, service=config.service_name, environment=environment):
        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
