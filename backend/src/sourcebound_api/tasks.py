import logging

from celery import Celery
from celery.exceptions import Reject
from celery.signals import worker_process_init, worker_process_shutdown
from kombu import Exchange, Queue

from sourcebound_api.config import get_settings
from sourcebound_api.ingestion import TransientIndexingError, mark_version_failed, parse_document_version
from sourcebound_api.tracing import configure_tracing, shutdown_tracing

settings = get_settings()
logger = logging.getLogger(__name__)


@worker_process_init.connect
def initialize_worker_tracing(**_kwargs) -> None:
    configure_tracing()


@worker_process_shutdown.connect
def close_worker_tracing(**_kwargs) -> None:
    shutdown_tracing()


celery_app = Celery("sourcebound", broker=settings.celery_broker_url)
celery_app.conf.update(
    task_default_queue="document-ingestion",
    task_default_exchange="sourcebound",
    task_default_exchange_type="direct",
    task_default_routing_key="document-ingestion",
    # RabbitMQ 4.3 disallows transient non-exclusive queues by default.
    # Celery 5.6 supports exclusive remote-control queues; do not make the
    # broker permit a deprecated queue type globally.
    control_queue_exclusive=True,
    task_queues=(
        Queue(
            "document-ingestion",
            Exchange("sourcebound", type="direct", durable=True),
            routing_key="document-ingestion",
            durable=True,
            queue_arguments={
                "x-dead-letter-exchange": "sourcebound.dead",
                "x-dead-letter-routing-key": "document-ingestion.failed",
            },
        ),
        Queue(
            "document-ingestion.failed",
            Exchange("sourcebound.dead", type="direct", durable=True),
            routing_key="document-ingestion.failed",
            durable=True,
        ),
    ),
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_delivery_mode=2,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
)


@celery_app.task(bind=True, name="sourcebound.parse_document_version", max_retries=3)
def parse_document_version_task(task, version_id: str) -> None:
    try:
        parse_document_version(version_id, task.request.headers or {})
    except TransientIndexingError as exc:
        if task.request.retries < task.max_retries:
            countdown = min(30 * (2 ** task.request.retries), 300)
            raise task.retry(exc=exc, countdown=countdown)
        try:
            mark_version_failed(version_id, "Indexing retry limit reached; inspect the failed-queue message and retry after resolving the dependency issue.")
        except Exception:
            logger.warning("Could not persist final indexing failure for version %s", version_id)
        raise Reject("Indexing retry limit reached", requeue=False) from exc
    except Exception as exc:
        raise Reject("Document processing failed permanently", requeue=False) from exc
