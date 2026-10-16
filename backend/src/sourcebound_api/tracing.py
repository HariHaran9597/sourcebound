"""Privacy-conscious OpenTelemetry setup and W3C context propagation."""

from collections.abc import Mapping

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from sourcebound_api.config import get_settings

_provider: TracerProvider | None = None
tracer = trace.get_tracer("sourcebound", "0.1.0")


def configure_tracing() -> None:
    """Enable batched OTLP/HTTP trace export when explicitly configured."""
    global _provider
    settings = get_settings()
    if not settings.tracing_enabled or _provider is not None:
        return
    provider = TracerProvider(
        resource=Resource.create({
            "service.name": settings.otel_service_name,
            "service.namespace": "sourcebound",
        }),
        sampler=ParentBased(root=TraceIdRatioBased(settings.trace_sample_ratio)),
    )
    exporter = OTLPSpanExporter(
        endpoint=settings.trace_otlp_endpoint,
        timeout=settings.trace_export_timeout_seconds,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    # Propagate trace context only. Baggage is intentionally disabled so arbitrary
    # caller values cannot travel through the queue or become telemetry metadata.
    propagate.set_global_textmap(TraceContextTextMapPropagator())
    _provider = provider


def shutdown_tracing() -> None:
    if _provider is not None:
        _provider.shutdown()


def extract_context(carrier: Mapping[str, str] | None) -> Context:
    return propagate.extract(dict(carrier or {}))


def inject_context() -> dict[str, str]:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


def current_trace_id() -> str | None:
    span_context = trace.get_current_span().get_span_context()
    return f"{span_context.trace_id:032x}" if span_context.is_valid else None


def mark_span_error(span: Span, error: Exception) -> None:
    """Mark failure without recording exception messages or stack traces."""
    span.set_attribute("error.type", type(error).__name__)
    span.set_status(Status(StatusCode.ERROR))


def mark_span_failed(span: Span) -> None:
    span.set_status(Status(StatusCode.ERROR))


__all__ = [
    "SpanKind",
    "configure_tracing",
    "current_trace_id",
    "extract_context",
    "inject_context",
    "mark_span_error",
    "mark_span_failed",
    "shutdown_tracing",
    "tracer",
]
