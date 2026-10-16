import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sourcebound_api.db import SessionLocal
from sourcebound_api.models import DocumentVersion
from sourcebound_api.tracing import extract_context, mark_span_error, tracer

logger = logging.getLogger(__name__)


class TransientIndexingError(RuntimeError):
    """An indexing failure that is safe to retry after parsing has completed."""


def mark_version_failed(version_id: str, message: str) -> None:
    with SessionLocal() as session:
        version = session.get(DocumentVersion, version_id)
        if version is not None:
            version.status = "failed"
            version.error_message = message[:2000]
            version.processed_at = datetime.now(UTC)
            session.commit()


def parse_document_version(version_id: str, trace_headers: dict[str, str] | None = None) -> None:
    """Process a version under its API enqueue trace when W3C headers are available."""
    context = extract_context(trace_headers) if trace_headers is not None else None
    with tracer.start_as_current_span(
        "sourcebound.ingestion.process",
        context=context,
        record_exception=False,
    ) as span:
        try:
            _parse_document_version(version_id)
        except Exception as exc:
            mark_span_error(span, exc)
            raise


def _parse_document_version(version_id: str) -> None:
    """Parse one stored PDF and persist its structured Markdown for later indexing."""
    with SessionLocal() as session:
        version = session.get(DocumentVersion, version_id)
        if version is None:
            logger.warning("Skipping missing document version %s", version_id)
            return
        source_path = Path(version.storage_path)
        parsed_path = source_path.with_suffix(".md")
        structured_path = source_path.with_suffix(".json")
        already_parsed = parsed_path.is_file() and structured_path.is_file()
        version.processing_attempts = (version.processing_attempts or 0) + 1
        version.processing_started_at = datetime.now(UTC)
        version.processed_at = None
        version.status = "indexing" if already_parsed else "processing"
        version.error_message = None
        session.commit()

    try:
        if not already_parsed:
            from docling.document_converter import DocumentConverter

            try:
                with tracer.start_as_current_span("sourcebound.ingestion.parse_pdf", record_exception=False) as span:
                    result = DocumentConverter().convert(str(source_path))
                    markdown = result.document.export_to_markdown()
                    structured = result.document.export_to_dict()
                    page_count = len(result.document.pages)
                    span.set_attribute("sourcebound.document.page_count", page_count)
            except Exception as exc:
                mark_span_error(span, exc)
                raise
            parsed_path.parent.mkdir(parents=True, exist_ok=True)
            parsed_path.write_text(markdown, encoding="utf-8")
            structured_path.write_text(json.dumps(structured, ensure_ascii=False), encoding="utf-8")

            with SessionLocal() as session:
                version = session.get(DocumentVersion, version_id)
                if version is None:
                    return
                version.parsed_path = str(parsed_path.resolve())
                version.structured_path = str(structured_path.resolve())
                version.page_count = page_count
                version.parser_name = "docling"
                version.status = "indexing"
                version.error_message = None
                session.commit()
        from sourcebound_api.retrieval import index_document_version

        try:
            with tracer.start_as_current_span("sourcebound.ingestion.index", record_exception=False) as index_span:
                try:
                    index_document_version(version_id)
                except Exception as exc:
                    mark_span_error(index_span, exc)
                    raise
        except ValueError:
            raise
        except Exception as exc:
            raise TransientIndexingError(type(exc).__name__) from exc
        with SessionLocal() as session:
            version = session.get(DocumentVersion, version_id)
            if version is not None:
                version.status = "ready"
                version.processed_at = datetime.now(UTC)
                session.commit()
    except TransientIndexingError as exc:
        logger.warning("Transient indexing failure for version %s (%s)", version_id, type(exc.__cause__ or exc).__name__)
        with SessionLocal() as session:
            version = session.get(DocumentVersion, version_id)
            if version is not None:
                version.status = "indexing"
                version.error_message = f"Temporary indexing error; retry scheduled ({type(exc.__cause__ or exc).__name__})."
                session.commit()
        raise
    except Exception as exc:
        logger.exception("Document parsing failed for version %s", version_id)
        with SessionLocal() as session:
            version = session.get(DocumentVersion, version_id)
            if version is not None:
                version.status = "failed"
                version.error_message = str(exc)[:2000]
                version.processed_at = datetime.now(UTC)
                session.commit()
        raise
