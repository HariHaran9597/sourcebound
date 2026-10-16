"""Low-cardinality HTTP metrics and privacy-aware structured request logs."""

import json
import logging
from math import ceil
import time
from secrets import token_hex

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sourcebound_api.tracing import current_trace_id

logger = logging.getLogger("sourcebound.http")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False
HTTP_REQUESTS = Counter(
    "sourcebound_http_requests_total",
    "Completed HTTP requests",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "sourcebound_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 90),
)
DOCUMENT_VERSION_STATUS = Gauge(
    "sourcebound_document_versions",
    "Document versions by processing status",
    ("status",),
)
DOCUMENT_PROCESSING_ATTEMPTS = Gauge(
    "sourcebound_document_processing_attempts",
    "Persisted processing attempts summed by each version's current status",
    ("status",),
)
DOCUMENT_PROCESSING_DURATION = Gauge(
    "sourcebound_document_processing_duration_seconds",
    "Latest-attempt duration quantiles over the most recent completed versions",
    ("status", "quantile"),
)
DOCUMENT_PROCESSING_DURATION_SAMPLES = Gauge(
    "sourcebound_document_processing_duration_samples",
    "Number of recent completed document versions used for duration quantiles",
    ("status",),
)
INGESTION_QUEUE_MESSAGES = Gauge(
    "sourcebound_ingestion_queue_messages",
    "RabbitMQ messages waiting in Sourcebound ingestion queues",
    ("queue",),
)
INGESTION_QUEUE_CONSUMERS = Gauge(
    "sourcebound_ingestion_queue_consumers",
    "RabbitMQ consumers attached to Sourcebound ingestion queues",
    ("queue",),
)
INGESTION_QUEUE_AVAILABLE = Gauge(
    "sourcebound_ingestion_queue_available",
    "Whether an ingestion queue exists and could be inspected during the last metrics scrape",
    ("queue",),
)
OPERATIONAL_DEPENDENCY_UP = Gauge(
    "sourcebound_operational_dependency_up",
    "Whether Sourcebound could query an operational dependency during the last metrics scrape",
    ("dependency",),
)
VERSION_STATUSES = ("queued", "processing", "indexing", "ready", "failed", "other")
TERMINAL_PROCESSING_STATUSES = ("ready", "failed")
PROCESSING_DURATION_QUANTILES = (0.5, 0.95)
PROCESSING_DURATION_SAMPLE_LIMIT = 500
INGESTION_QUEUES = {
    "pending": "document-ingestion",
    "failed": "document-ingestion.failed",
}


async def observe_request(request: Request, call_next) -> Response:
    started = time.perf_counter()
    request_id = token_hex(12)
    request.state.request_id = request_id
    status_code = 500
    response: Response | None = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        route_object = request.scope.get("route")
        route = getattr(route_object, "path", "unmatched")
        elapsed = time.perf_counter() - started
        HTTP_REQUESTS.labels(request.method, route, str(status_code)).inc()
        HTTP_DURATION.labels(request.method, route).observe(elapsed)
        # Never log request/response bodies, query strings, document IDs, subjects, or tokens.
        logger.info(json.dumps({
            "event": "http_request_complete",
            "request_id": request_id,
            "trace_id": current_trace_id(),
            "method": request.method,
            "route": route,
            "status_code": status_code,
            "duration_ms": round(elapsed * 1000, 2),
        }, separators=(",", ":")))


def metrics_response() -> Response:
    _refresh_operational_metrics()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _refresh_operational_metrics() -> None:
    # These are shared system-of-record views, unlike process-local worker counters
    # that would be incomplete across Celery's prefork processes.
    for status in VERSION_STATUSES:
        DOCUMENT_VERSION_STATUS.labels(status).set(0)
        DOCUMENT_PROCESSING_ATTEMPTS.labels(status).set(0)
    for status in TERMINAL_PROCESSING_STATUSES:
        DOCUMENT_PROCESSING_DURATION_SAMPLES.labels(status).set(0)
        for quantile in PROCESSING_DURATION_QUANTILES:
            DOCUMENT_PROCESSING_DURATION.labels(status, str(quantile)).set(0)
    for queue in INGESTION_QUEUES:
        INGESTION_QUEUE_MESSAGES.labels(queue).set(0)
        INGESTION_QUEUE_CONSUMERS.labels(queue).set(0)
        INGESTION_QUEUE_AVAILABLE.labels(queue).set(0)
    OPERATIONAL_DEPENDENCY_UP.labels("database").set(0)
    OPERATIONAL_DEPENDENCY_UP.labels("broker").set(0)

    try:
        from sqlalchemy import func, select

        from sourcebound_api.db import engine
        from sourcebound_api.models import DocumentVersion

        with engine.connect() as connection:
            counts = connection.execute(
                select(DocumentVersion.status, func.count()).group_by(DocumentVersion.status)
            ).all()
            attempts = connection.execute(
                select(DocumentVersion.status, func.sum(DocumentVersion.processing_attempts))
                .group_by(DocumentVersion.status)
            ).all()
            recent_finished = connection.execute(
                select(
                    DocumentVersion.status,
                    DocumentVersion.processing_started_at,
                    DocumentVersion.processed_at,
                )
                .where(
                    DocumentVersion.status.in_(TERMINAL_PROCESSING_STATUSES),
                    DocumentVersion.processing_started_at.is_not(None),
                    DocumentVersion.processed_at.is_not(None),
                )
                .order_by(DocumentVersion.processed_at.desc())
                .limit(PROCESSING_DURATION_SAMPLE_LIMIT)
            ).all()
        for status, count in counts:
            label = status if status in VERSION_STATUSES else "other"
            DOCUMENT_VERSION_STATUS.labels(label).inc(count)
        for status, count in attempts:
            label = status if status in VERSION_STATUSES else "other"
            DOCUMENT_PROCESSING_ATTEMPTS.labels(label).set(count or 0)
        durations: dict[str, list[float]] = {status: [] for status in TERMINAL_PROCESSING_STATUSES}
        for status, started_at, finished_at in recent_finished:
            if status not in durations:
                continue
            seconds = max(0.0, (finished_at - started_at).total_seconds())
            durations[status].append(seconds)
        for status, values in durations.items():
            DOCUMENT_PROCESSING_DURATION_SAMPLES.labels(status).set(len(values))
            if not values:
                continue
            values.sort()
            for quantile in PROCESSING_DURATION_QUANTILES:
                index = max(0, min(len(values) - 1, ceil(quantile * len(values)) - 1))
                DOCUMENT_PROCESSING_DURATION.labels(status, str(quantile)).set(values[index])
        OPERATIONAL_DEPENDENCY_UP.labels("database").set(1)
    except Exception:
        # Keep /metrics scrapeable during a dependency incident; dependency_up=0
        # tells operators that the reported state counts are unavailable.
        pass

    try:
        from kombu import Connection

        from sourcebound_api.config import get_settings

        settings = get_settings()
        if settings.parse_mode != "celery":
            # RabbitMQ is not a runtime dependency for local background-task mode.
            OPERATIONAL_DEPENDENCY_UP.labels("broker").set(1)
            return
        with Connection(settings.celery_broker_url, connect_timeout=2) as connection:
            OPERATIONAL_DEPENDENCY_UP.labels("broker").set(1)
            for label, queue_name in INGESTION_QUEUES.items():
                channel = connection.channel()
                try:
                    queue_info = channel.queue_declare(queue=queue_name, passive=True)
                    INGESTION_QUEUE_MESSAGES.labels(label).set(queue_info.message_count)
                    INGESTION_QUEUE_CONSUMERS.labels(label).set(queue_info.consumer_count)
                    INGESTION_QUEUE_AVAILABLE.labels(label).set(1)
                except Exception:
                    # An undeclared queue is unavailable, not an empty queue.
                    INGESTION_QUEUE_AVAILABLE.labels(label).set(0)
                finally:
                    try:
                        channel.close()
                    except Exception:
                        OPERATIONAL_DEPENDENCY_UP.labels("broker").set(0)
    except Exception:
        OPERATIONAL_DEPENDENCY_UP.labels("broker").set(0)
