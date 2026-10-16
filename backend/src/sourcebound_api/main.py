import hashlib
import asyncio
import difflib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from kombu import Connection
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sourcebound_api.auth import Principal, get_principal
from sourcebound_api.config import get_settings
from sourcebound_api.db import Base, engine, get_session
from sourcebound_api.ingestion import parse_document_version
from sourcebound_api.models import AnswerTrace, AuditEvent, Document, DocumentRelationship, DocumentVersion, OrganizationGroup, OrganizationUsage
from sourcebound_api.observability import metrics_response, observe_request
from sourcebound_api.rate_limits import enforce_rate_limit
from sourcebound_api.retrieval import PROMPT_VERSION, active_reranker_model, delete_document_points, generate_answer, requests_relationship_expansion, requests_version_history, retrieval_version, search, update_document_visibility
from sourcebound_api.tasks import parse_document_version_task
from sourcebound_api.tracing import SpanKind, configure_tracing, current_trace_id, extract_context, inject_context, mark_span_error, mark_span_failed, shutdown_tracing, tracer

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_tracing()
    try:
        if engine.dialect.name == "sqlite" and sqlite3.sqlite_version_info < (3, 35, 0):
            raise RuntimeError("Sourcebound rate limiting requires SQLite 3.35 or newer; use PostgreSQL for deployment.")
        if settings.auth_mode == "disabled":
            logger.warning("Authentication is disabled. Do not expose this Sourcebound instance or upload sensitive documents.")
        if settings.auto_create_schema:
            # Local convenience; container deployments apply Alembic migrations first.
            Base.metadata.create_all(bind=engine)
        settings.storage_root.mkdir(parents=True, exist_ok=True)
        yield
    finally:
        shutdown_tracing()


app = FastAPI(
    title="Sourcebound API",
    summary="Evidence-first answers across changing organizational knowledge.",
    version="0.2.0",
    lifespan=lifespan,
)

# These guards bound work per API process. Shared fixed-window rate limits
# remain database-coordinated; deployments with multiple replicas should also
# set ingress concurrency/body limits at their reverse proxy.
_api_capacity = asyncio.Semaphore(settings.api_max_in_flight)
_ask_capacity = asyncio.Semaphore(settings.ask_max_in_flight)
_ingestion_capacity = asyncio.Semaphore(settings.ingestion_max_in_flight)


@app.middleware("http")
async def enforce_process_capacity(request: Request, call_next):
    acquired: list[asyncio.Semaphore] = []
    path = request.url.path
    method = request.method
    semaphores = [_api_capacity]
    if method == "POST" and path == "/api/v1/ask":
        semaphores.append(_ask_capacity)
    elif method == "POST" and (
        path == "/api/v1/documents"
        or path.endswith("/versions")
        or path.endswith("/reprocess")
    ):
        semaphores.append(_ingestion_capacity)
    try:
        for semaphore in semaphores:
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=settings.capacity_wait_seconds)
            except TimeoutError:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Service is at capacity. Retry shortly."},
                    headers={"Retry-After": "2"},
                )
            acquired.append(semaphore)
        return await call_next(request)
    finally:
        for semaphore in reversed(acquired):
            semaphore.release()


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unavailable")
    logger.error(json.dumps({
        "event": "unhandled_api_error",
        "request_id": request_id,
        "error_type": type(exc).__name__,
    }, separators=(",", ":")))
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred.", "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=[
        "X-Request-ID",
        "X-Trace-ID",
        "RateLimit-Limit",
        "RateLimit-Remaining",
        "RateLimit-Reset",
        "RateLimit-Scope",
        "Retry-After",
    ],
)
app.middleware("http")(observe_request)


@app.middleware("http")
async def trace_request(request: Request, call_next):
    with tracer.start_as_current_span(
        "http.server.request",
        context=extract_context(request.headers),
        kind=SpanKind.SERVER,
        record_exception=False,
    ) as span:
        span.set_attribute("http.request.method", request.method)
        try:
            response = await call_next(request)
        except Exception as exc:
            mark_span_error(span, exc)
            raise
        route = getattr(request.scope.get("route"), "path", "unmatched")
        span.set_attribute("http.route", route)
        span.set_attribute("http.response.status_code", response.status_code)
        request_id = getattr(request.state, "request_id", None)
        if request_id:
            span.set_attribute("sourcebound.request.id", request_id)
        if response.status_code >= 500:
            mark_span_failed(span)
        trace_id = current_trace_id()
        if trace_id:
            response.headers["X-Trace-ID"] = trace_id
        return response

SessionDep = Annotated[Session, Depends(get_session)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class VersionResponse(BaseModel):
    id: str
    version: int
    status: str
    sha256: str
    byte_size: int
    page_count: int | None
    parser: str | None
    error: str | None
    created_at: datetime
    processed_at: datetime | None


class DocumentResponse(BaseModel):
    id: str
    title: str
    filename: str
    latest_version: int
    access_groups: list[str] | None
    status: str
    created_at: datetime
    updated_at: datetime


class DocumentDetailResponse(DocumentResponse):
    versions: list[VersionResponse]


class AskRequest(BaseModel):
    question: str


class CitationResponse(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    filename: str
    version_id: str
    version_number: int
    page_numbers: list[int]
    text: str
    score: float


class ConflictResponse(BaseModel):
    conflict_id: str
    summary: str
    citation_chunk_ids: list[str]
    disposition: Literal["unreviewed", "confirmed", "dismissed"] = "unreviewed"
    review_note: str | None = None
    reviewed_at: datetime | None = None


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    conflicts: list[ConflictResponse]
    abstained: bool
    trace_id: str
    observability_trace_id: str | None = None
    model_name: str
    embedding_model: str
    reranker_model: str | None
    retrieval_version: str
    retrieval_scope: Literal[
        "latest", "latest_plus_history", "latest_plus_related", "latest_plus_history_and_related"
    ]
    prompt_version: str
    history_enabled: bool
    history_saved: bool


class AnswerTraceResponse(BaseModel):
    trace_id: str
    question: str
    answer: str
    citations: list[CitationResponse]
    conflicts: list[ConflictResponse]
    abstained: bool
    model_name: str
    embedding_model: str
    reranker_model: str | None
    retrieval_version: str
    retrieval_scope: Literal[
        "latest", "latest_plus_history", "latest_plus_related", "latest_plus_history_and_related"
    ]
    prompt_version: str
    created_at: datetime
    redacted: bool = False


class AnswerHistoryResponse(BaseModel):
    enabled: bool
    retention_days: int
    traces: list[AnswerTraceResponse]


class AuditEventResponse(BaseModel):
    id: str
    actor_subject: str
    action: str
    resource_type: str
    resource_id: str
    details: dict
    created_at: datetime


class DocumentChangesResponse(BaseModel):
    document_id: str
    document_title: str
    from_version: int
    to_version: int
    additions: int
    deletions: int
    unified_diff: str


class RelationshipCreate(BaseModel):
    target_document_id: str = Field(min_length=36, max_length=36)
    relationship_type: Literal["related_to", "implements", "supersedes", "depends_on"] = "related_to"


class RelationshipResponse(BaseModel):
    id: str
    source_document_id: str
    source_document_title: str
    target_document_id: str
    target_document_title: str
    relationship_type: Literal["related_to", "implements", "supersedes", "depends_on"]
    created_at: datetime


class AccessUpdate(BaseModel):
    access_groups: list[str] | None = Field(max_length=100)


class AccessGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def strip_group_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Group name cannot be blank.")
        return value


class AccessGroupResponse(BaseModel):
    id: str
    name: str
    assigned_document_count: int
    created_at: datetime


class OrganizationUsageResponse(BaseModel):
    document_count: int
    document_limit: int
    storage_bytes: int
    storage_limit_bytes: int
    updated_at: datetime


class ConflictReviewRequest(BaseModel):
    disposition: Literal["confirmed", "dismissed"]
    review_note: str | None = Field(default=None, max_length=1000)


def _merge_relationship_passages(
    primary: list[dict],
    related: list[dict],
    include_history: bool,
    limit: int = 8,
) -> list[dict]:
    """Keep lineage pairs and a curated neighbor in the bounded answer context."""
    unique: dict[str, dict] = {}
    for passage in sorted([*primary, *related], key=lambda item: float(item.get("score", 0)), reverse=True):
        unique.setdefault(str(passage.get("chunk_id")), passage)
    ranked = list(unique.values())
    required: list[dict] = []
    if include_history:
        by_document: dict[str, dict[bool, dict]] = {}
        for passage in primary:
            versions = by_document.setdefault(str(passage.get("document_id")), {})
            versions.setdefault(bool(passage.get("is_latest")), passage)
        pairs = [versions for versions in by_document.values() if True in versions and False in versions]
        if pairs:
            pair = min(pairs, key=lambda versions: sum(
                next(i for i, item in enumerate(primary) if item.get("chunk_id") == versions[key].get("chunk_id"))
                for key in (True, False)
            ))
            required.extend((pair[True], pair[False]))
    if related:
        required.append(max(related, key=lambda item: float(item.get("score", 0))))
    selected_by_id = {str(item.get("chunk_id")): item for item in required}
    for passage in ranked:
        if len(selected_by_id) >= limit:
            break
        selected_by_id.setdefault(str(passage.get("chunk_id")), passage)
    return sorted(selected_by_id.values(), key=lambda passage: float(passage.get("score", 0)), reverse=True)


def _version_response(version: DocumentVersion) -> VersionResponse:
    return VersionResponse(
        id=version.id,
        version=version.version_number,
        status=version.status,
        sha256=version.sha256,
        byte_size=version.byte_size,
        page_count=version.page_count,
        parser=version.parser_name,
        error=version.error_message,
        created_at=version.created_at,
        processed_at=version.processed_at,
    )


def _document_response(document: Document) -> DocumentResponse:
    latest = document.versions[-1] if document.versions else None
    return DocumentResponse(
        id=document.id,
        title=document.title,
        filename=document.original_filename,
        latest_version=document.latest_version,
        access_groups=document.access_groups,
        status=latest.status if latest else "queued",
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _safe_filename(filename: str | None) -> str:
    leaf = (filename or "document.pdf").replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[^\w.() -]", "_", leaf, flags=re.UNICODE).strip(" .")
    return (cleaned or "document.pdf")[:500]


def _can_access(document: Document, principal: Principal) -> bool:
    return "sourcebound_admin" in principal.roles or document.access_groups is None or bool(set(document.access_groups).intersection(principal.groups))


def _sync_identity_groups(session: Session, principal: Principal) -> None:
    """Register verified identity groups so admins can reuse them in access policies."""
    names = sorted(set(name.strip() for name in principal.groups if name.strip()))
    if not names:
        return
    dialect = session.get_bind().dialect.name
    insert = postgres_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
    if insert is None:
        raise HTTPException(status_code=503, detail="Access-group synchronization is unavailable for this database backend.")
    session.execute(insert(OrganizationGroup).values([
        {
            "id": str(uuid4()),
            "organization_id": principal.organization_id,
            "name": name,
            "created_by": principal.subject,
            "created_at": datetime.now(UTC),
        }
        for name in names
    ]).on_conflict_do_nothing(index_elements=["organization_id", "name"]))


def _lock_organization_usage(session: Session, organization_id: str) -> OrganizationUsage:
    """Serialize quota checks and accounting for this organization across API replicas."""
    dialect = session.get_bind().dialect.name
    insert = postgres_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
    if insert is None:
        raise HTTPException(status_code=503, detail="Organization quota accounting is unavailable for this database backend.")
    session.execute(insert(OrganizationUsage).values(
        organization_id=organization_id,
        document_count=0,
        storage_bytes=0,
        updated_at=datetime.now(UTC),
    ).on_conflict_do_nothing(index_elements=["organization_id"]))
    usage = session.scalar(select(OrganizationUsage).where(
        OrganizationUsage.organization_id == organization_id,
    ).with_for_update())
    if usage is None:
        raise HTTPException(status_code=503, detail="Organization quota accounting is unavailable.")
    return usage


def _check_storage_quota(usage: OrganizationUsage, incoming_bytes: int) -> None:
    if usage.storage_bytes + incoming_bytes > settings.organization_max_storage_bytes:
        raise HTTPException(
            status_code=409,
            detail="Organization storage quota would be exceeded. Remove source versions or ask an administrator to raise the configured limit.",
        )


def _record_audit_event(
    session: Session,
    principal: Principal,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict | None = None,
) -> None:
    session.add(AuditEvent(
        organization_id=principal.organization_id,
        actor_subject=principal.subject,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        created_at=datetime.now(UTC),
    ))


def _save_answer_trace(
    session: Session,
    principal: Principal,
    trace_id: str,
    question: str,
    answer: str,
    citations: list[CitationResponse],
    conflicts: list[ConflictResponse],
    abstained: bool,
    retrieval_version_name: str,
    retrieval_scope: str,
    reranker_model_name: str | None,
) -> bool:
    if not settings.store_answer_traces:
        return False
    now = datetime.now(UTC)
    session.add(AnswerTrace(
        id=trace_id,
        organization_id=principal.organization_id,
        subject=principal.subject,
        question=question,
        answer=answer,
        citations=[citation.model_dump(mode="json") for citation in citations],
        conflicts=[conflict.model_dump(mode="json") for conflict in conflicts],
        abstained=abstained,
        model_name=settings.llm_model,
        embedding_model=settings.embedding_model,
        reranker_model=reranker_model_name,
        retrieval_version=retrieval_version_name,
        retrieval_scope=retrieval_scope,
        prompt_version=PROMPT_VERSION,
        created_at=now,
        expires_at=now + timedelta(days=settings.answer_trace_retention_days),
    ))
    try:
        session.commit()
        return True
    except Exception as exc:
        session.rollback()
        logger.warning("Could not persist answer trace %s (%s)", trace_id, type(exc).__name__)
        return False


def _trace_response(trace: AnswerTrace, session: Session, principal: Principal) -> AnswerTraceResponse:
    citation_rows = trace.citations or []
    document_ids = {row.get("document_id") for row in citation_rows if row.get("document_id")}
    allowed_ids: set[str] = set()
    if document_ids:
        documents = session.scalars(select(Document).where(
            Document.organization_id == principal.organization_id,
            Document.id.in_(document_ids),
        )).all()
        allowed_ids = {document.id for document in documents if _can_access(document, principal)}
    redacted = bool(document_ids - allowed_ids)
    citations = [] if redacted else [CitationResponse(**row) for row in citation_rows]
    conflicts = [] if redacted else [ConflictResponse(**row) for row in (trace.conflicts or [])]
    return AnswerTraceResponse(
        trace_id=trace.id,
        question="" if redacted else trace.question,
        answer="" if redacted else trace.answer,
        citations=citations,
        conflicts=conflicts,
        abstained=trace.abstained,
        model_name=trace.model_name,
        embedding_model=trace.embedding_model,
        reranker_model=trace.reranker_model,
        retrieval_version=trace.retrieval_version,
        retrieval_scope=trace.retrieval_scope,
        prompt_version=trace.prompt_version,
        created_at=trace.created_at,
        redacted=redacted,
    )


async def _store_pdf(upload: UploadFile, destination: Path) -> tuple[str, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    descriptor, temp_name = tempfile.mkstemp(prefix=".upload-", suffix=".part", dir=destination.parent)
    os.close(descriptor)
    temp_path = Path(temp_name)

    try:
        with temp_path.open("wb") as target:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit.",
                    )
                digest.update(chunk)
                target.write(chunk)

        if total == 0:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")
        with temp_path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise HTTPException(status_code=415, detail="Only valid PDF files are supported in this phase.")
        os.replace(temp_path, destination)
        return digest.hexdigest(), total
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


async def _traced_search(
    question: str,
    organization_id: str,
    groups: tuple[str, ...],
    allowed_document_ids: list[str],
    include_history: bool,
    role: str,
) -> list[dict]:
    with tracer.start_as_current_span("sourcebound.retrieval.search", record_exception=False) as span:
        span.set_attribute("sourcebound.retrieval.role", role)
        span.set_attribute("sourcebound.retrieval.history_enabled", include_history)
        try:
            results = await asyncio.to_thread(
                search, question, organization_id, groups,
                allowed_document_ids, include_history=include_history,
            )
        except Exception as exc:
            mark_span_error(span, exc)
            raise
        span.set_attribute("sourcebound.retrieval.result_count", len(results))
        return results


def _schedule_parse(version_id: str, background_tasks: BackgroundTasks) -> None:
    with tracer.start_as_current_span("sourcebound.ingestion.enqueue", record_exception=False) as span:
        carrier = inject_context()
        try:
            if settings.parse_mode == "celery":
                parse_document_version_task.apply_async(args=[version_id], headers=carrier)
            else:
                # Local-only convenience. Deployments use the durable Celery/RabbitMQ queue.
                background_tasks.add_task(parse_document_version, version_id, carrier)
        except Exception as exc:
            mark_span_error(span, exc)
            raise


@app.get("/api/v1/health", response_model=HealthResponse, tags=["Operations"])
def health() -> HealthResponse:
    """Report that the API process is running. This does not mean dependencies are ready."""
    return HealthResponse(status="ok", service="sourcebound-api", version="0.2.0")


@app.get("/api/v1/health/ready", tags=["Operations"])
def readiness() -> dict[str, str]:
    """Check API dependencies for orchestration and load-balancer readiness."""
    try:
        with engine.connect() as connection:
            connection.execute(select(1))
        from sourcebound_api.retrieval import _client

        _client().get_collections()
        if settings.parse_mode == "celery":
            with Connection(settings.celery_broker_url, connect_timeout=2) as broker:
                broker.connect()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database, vector search, or required processing broker is not ready.") from exc
    return {"status": "ready", "service": "sourcebound-api"}


@app.get("/api/v1/groups", response_model=list[AccessGroupResponse], tags=["Access control"])
def list_access_groups(session: SessionDep, principal: PrincipalDep) -> list[AccessGroupResponse]:
    if "sourcebound_admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="Workspace administrator role required.")
    groups = session.scalars(select(OrganizationGroup).where(
        OrganizationGroup.organization_id == principal.organization_id,
    ).order_by(OrganizationGroup.name)).all()
    assigned_counts: dict[str, int] = {}
    policies = session.scalars(select(Document.access_groups).where(
        Document.organization_id == principal.organization_id,
        Document.access_groups.is_not(None),
    )).all()
    for policy in policies:
        for name in set(policy or []):
            assigned_counts[name] = assigned_counts.get(name, 0) + 1
    return [AccessGroupResponse(
        id=group.id,
        name=group.name,
        assigned_document_count=assigned_counts.get(group.name, 0),
        created_at=group.created_at,
    ) for group in groups]


@app.get("/api/v1/organization/usage", response_model=OrganizationUsageResponse, tags=["Access control"])
def get_organization_usage(session: SessionDep, principal: PrincipalDep) -> OrganizationUsageResponse:
    if "sourcebound_admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="Workspace administrator role required.")
    usage = _lock_organization_usage(session, principal.organization_id)
    session.commit()
    return OrganizationUsageResponse(
        document_count=usage.document_count,
        document_limit=settings.organization_max_documents,
        storage_bytes=usage.storage_bytes,
        storage_limit_bytes=settings.organization_max_storage_bytes,
        updated_at=usage.updated_at,
    )


@app.post("/api/v1/groups", response_model=AccessGroupResponse, status_code=201, tags=["Access control"])
def create_access_group(
    payload: AccessGroupCreate,
    session: SessionDep,
    principal: PrincipalDep,
) -> AccessGroupResponse:
    if "sourcebound_admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="Workspace administrator role required.")
    group = OrganizationGroup(
        organization_id=principal.organization_id,
        name=payload.name,
        created_by=principal.subject,
    )
    session.add(group)
    try:
        # Populate the database-generated UUID before using it as the audit resource ID.
        session.flush()
        _record_audit_event(session, principal, "access_group.created", "access_group", group.id, {
            "name": group.name,
        })
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="That access group already exists.") from exc
    return AccessGroupResponse(
        id=group.id,
        name=group.name,
        assigned_document_count=0,
        created_at=group.created_at,
    )


@app.delete("/api/v1/groups/{group_id}", status_code=204, tags=["Access control"])
def delete_access_group(
    group_id: str,
    session: SessionDep,
    principal: PrincipalDep,
) -> Response:
    if "sourcebound_admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="Workspace administrator role required.")
    group = session.scalar(select(OrganizationGroup).where(
        OrganizationGroup.id == group_id,
        OrganizationGroup.organization_id == principal.organization_id,
    ).with_for_update())
    if group is None:
        raise HTTPException(status_code=404, detail="Access group not found.")
    policies = session.scalars(select(Document.access_groups).where(
        Document.organization_id == principal.organization_id,
        Document.access_groups.is_not(None),
    )).all()
    assigned_document_count = sum(group.name in set(policy or []) for policy in policies)
    if assigned_document_count:
        raise HTTPException(
            status_code=409,
            detail=f"This group is assigned to {assigned_document_count} document policies. Remove it from those policies first.",
        )
    _record_audit_event(session, principal, "access_group.deleted", "access_group", group.id, {
        "name": group.name,
    })
    session.delete(group)
    session.commit()
    return Response(status_code=204)


@app.get("/api/v1/metrics", include_in_schema=False, tags=["Operations"])
def metrics(authorization: str | None = Header(default=None)) -> Response:
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics are disabled.")
    if settings.auth_mode == "oidc":
        principal = get_principal(authorization)
        if "sourcebound_admin" not in principal.roles:
            raise HTTPException(status_code=403, detail="Workspace administrator role required.")
    return metrics_response()


@app.post("/api/v1/ask", response_model=AskResponse, tags=["Answers"])
async def ask(payload: AskRequest, request: Request, response: Response, principal: PrincipalDep, session: SessionDep) -> AskResponse:
    enforce_rate_limit(
        session, request, response, principal,
        policy="ask", limit=settings.ask_rate_limit_per_minute,
    )
    enforce_rate_limit(
        session, request, response, principal,
        policy="ask", limit=settings.organization_ask_rate_limit_per_minute,
        scope="organization",
    )
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Enter a question.")
    if len(question) > 2000:
        raise HTTPException(status_code=413, detail="Questions are limited to 2,000 characters.")
    try:
        include_history = requests_version_history(question)
        wants_relationships = requests_relationship_expansion(question)
        retrieval_version_name = retrieval_version()
        reranker_model_name = active_reranker_model()
        accessible_documents = session.scalars(
            select(Document).where(Document.organization_id == principal.organization_id)
        ).all()
        allowed_document_ids = [document.id for document in accessible_documents if _can_access(document, principal)]
        retrieval_groups = (*principal.groups, "__sourcebound_admin__") if "sourcebound_admin" in principal.roles else principal.groups
        passages = await _traced_search(
            question, principal.organization_id, retrieval_groups,
            allowed_document_ids, include_history, "primary",
        )
        expanded_related = False
        if wants_relationships and passages:
            seed_ids = list(dict.fromkeys(
                str(passage["document_id"]) for passage in passages[:8] if passage.get("document_id")
            ))[:4]
            allowed_ids = set(allowed_document_ids)
            documents_by_id = {document.id: document for document in accessible_documents if document.id in allowed_ids}
            related_edges = session.scalars(select(DocumentRelationship).where(
                DocumentRelationship.organization_id == principal.organization_id,
                or_(
                    DocumentRelationship.source_document_id.in_(seed_ids),
                    DocumentRelationship.target_document_id.in_(seed_ids),
                ),
            ).order_by(DocumentRelationship.created_at.desc()).limit(100)).all() if seed_ids else []
            related_context: dict[str, list[dict[str, str]]] = {}
            for edge in related_edges:
                if edge.source_document_id in seed_ids and edge.target_document_id in allowed_ids:
                    related_id = edge.target_document_id
                    curator_title = documents_by_id[edge.source_document_id].title
                    direction = "source"
                elif edge.target_document_id in seed_ids and edge.source_document_id in allowed_ids:
                    related_id = edge.source_document_id
                    curator_title = documents_by_id[edge.target_document_id].title
                    direction = "target"
                else:
                    continue
                if related_id in seed_ids:
                    continue
                related_context.setdefault(related_id, []).append({
                    "relationship_type": edge.relationship_type,
                    "linked_document_title": curator_title,
                    "direction": direction,
                })
            related_ids = list(related_context)[:8]
            if related_ids:
                related_passages = await _traced_search(
                    question, principal.organization_id, retrieval_groups,
                    related_ids, include_history, "related",
                )
                for passage in related_passages:
                    passage["relationship_context"] = related_context.get(str(passage.get("document_id")), [])
                if related_passages:
                    passages = _merge_relationship_passages(passages, related_passages, include_history)
                    expanded_related = True
        if include_history and expanded_related:
            retrieval_scope = "latest_plus_history_and_related"
        elif include_history:
            retrieval_scope = "latest_plus_history"
        elif expanded_related:
            retrieval_scope = "latest_plus_related"
        else:
            retrieval_scope = "latest"
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Search is unavailable. Check Qdrant, embedding, and reranker model setup.") from exc
    # The history key stays request-unique even if a caller reuses an inbound W3C trace ID.
    trace_id = f"sb_{request.state.request_id}"
    observability_trace_id = current_trace_id()
    if not passages:
        answer = "I could not find indexed evidence for that question. Add and index relevant documents first."
        abstained = True
        history_saved = _save_answer_trace(
            session, principal, trace_id, question, answer, [], [], abstained,
            retrieval_version_name, retrieval_scope, reranker_model_name,
        )
        return AskResponse(
            answer=answer,
            citations=[], abstained=abstained, trace_id=trace_id,
            observability_trace_id=observability_trace_id,
            conflicts=[],
            model_name=settings.llm_model, embedding_model=settings.embedding_model,
            reranker_model=reranker_model_name, retrieval_version=retrieval_version_name,
            retrieval_scope=retrieval_scope,
            prompt_version=PROMPT_VERSION,
            history_enabled=settings.store_answer_traces,
            history_saved=history_saved,
        )
    try:
        with tracer.start_as_current_span("sourcebound.answer.generate", record_exception=False) as span:
            try:
                answer, cited_passages, generated_conflicts = await generate_answer(question, passages)
            except Exception as exc:
                mark_span_error(span, exc)
                raise
            span.set_attribute("sourcebound.answer.citation_count", len(cited_passages))
            span.set_attribute("sourcebound.answer.conflict_count", len(generated_conflicts))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Answer generation is unavailable. Check the configured OpenAI-compatible model endpoint.") from exc
    citations = [CitationResponse(**passage) for passage in cited_passages]
    conflicts = [
        ConflictResponse(conflict_id=str(uuid4()), **conflict)
        for conflict in generated_conflicts
    ]
    abstained = "could not find enough evidence" in answer.lower() or "could not produce an answer" in answer.lower()
    history_saved = _save_answer_trace(
        session, principal, trace_id, question, answer, citations,
        [ConflictResponse(**conflict) for conflict in conflicts], abstained,
        retrieval_version_name, retrieval_scope, reranker_model_name,
    )
    return AskResponse(
        answer=answer,
        citations=citations,
        conflicts=[ConflictResponse(**conflict) for conflict in conflicts],
        abstained=abstained,
        trace_id=trace_id,
        observability_trace_id=observability_trace_id,
        model_name=settings.llm_model, embedding_model=settings.embedding_model,
        reranker_model=reranker_model_name, retrieval_version=retrieval_version_name,
        retrieval_scope=retrieval_scope,
        prompt_version=PROMPT_VERSION,
        history_enabled=settings.store_answer_traces,
        history_saved=history_saved,
    )


@app.get("/api/v1/history", response_model=AnswerHistoryResponse, tags=["Answers"])
def list_answer_history(
    session: SessionDep,
    principal: PrincipalDep,
    limit: int = Query(default=25, ge=1, le=100),
) -> AnswerHistoryResponse:
    if not settings.store_answer_traces:
        return AnswerHistoryResponse(enabled=False, retention_days=settings.answer_trace_retention_days, traces=[])
    now = datetime.now(UTC)
    session.execute(delete(AnswerTrace).where(
        AnswerTrace.organization_id == principal.organization_id,
        AnswerTrace.subject == principal.subject,
        AnswerTrace.expires_at <= now,
    ))
    session.commit()
    traces = session.scalars(select(AnswerTrace).where(
        AnswerTrace.organization_id == principal.organization_id,
        AnswerTrace.subject == principal.subject,
        AnswerTrace.expires_at > now,
    ).order_by(AnswerTrace.created_at.desc()).limit(limit)).all()
    return AnswerHistoryResponse(
        enabled=True,
        retention_days=settings.answer_trace_retention_days,
        traces=[_trace_response(trace, session, principal) for trace in traces],
    )


@app.get("/api/v1/audit-events", response_model=list[AuditEventResponse], tags=["Operations"])
def list_audit_events(
    session: SessionDep,
    principal: PrincipalDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AuditEventResponse]:
    if "sourcebound_admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="Workspace administrator role required.")
    events = session.scalars(select(AuditEvent).where(
        AuditEvent.organization_id == principal.organization_id,
    ).order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return [AuditEventResponse(
        id=event.id,
        actor_subject=event.actor_subject,
        action=event.action,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        details=event.details,
        created_at=event.created_at,
    ) for event in events]


@app.get("/api/v1/history/{trace_id}", response_model=AnswerTraceResponse, tags=["Answers"])
def get_answer_history(
    trace_id: str,
    session: SessionDep,
    principal: PrincipalDep,
) -> AnswerTraceResponse:
    if not settings.store_answer_traces:
        raise HTTPException(status_code=404, detail="Answer history is disabled.")
    trace = session.scalar(select(AnswerTrace).where(
        AnswerTrace.id == trace_id,
        AnswerTrace.organization_id == principal.organization_id,
        AnswerTrace.subject == principal.subject,
        AnswerTrace.expires_at > datetime.now(UTC),
    ))
    if trace is None:
        raise HTTPException(status_code=404, detail="Answer history entry not found.")
    return _trace_response(trace, session, principal)


@app.put(
    "/api/v1/history/{trace_id}/conflicts/{conflict_id}",
    response_model=ConflictResponse,
    tags=["Answers"],
)
def review_answer_conflict(
    trace_id: str,
    conflict_id: str,
    update: ConflictReviewRequest,
    session: SessionDep,
    principal: PrincipalDep,
) -> ConflictResponse:
    if not settings.store_answer_traces:
        raise HTTPException(status_code=404, detail="Answer history is disabled.")
    trace = session.scalar(select(AnswerTrace).where(
        AnswerTrace.id == trace_id,
        AnswerTrace.organization_id == principal.organization_id,
        AnswerTrace.subject == principal.subject,
        AnswerTrace.expires_at > datetime.now(UTC),
    ).with_for_update())
    if trace is None:
        raise HTTPException(status_code=404, detail="Answer history entry not found.")
    if _trace_response(trace, session, principal).redacted:
        raise HTTPException(status_code=404, detail="The cited sources are no longer accessible.")

    conflicts = [dict(conflict) for conflict in (trace.conflicts or [])]
    finding = next((item for item in conflicts if item.get("conflict_id") == conflict_id), None)
    if finding is None:
        raise HTTPException(status_code=404, detail="Conflict finding not found.")
    reviewed_at = datetime.now(UTC)
    finding["disposition"] = update.disposition
    review_note = (update.review_note or "").strip()
    finding["review_note"] = review_note or None
    finding["reviewed_at"] = reviewed_at.isoformat()
    trace.conflicts = conflicts
    _record_audit_event(session, principal, "answer.conflict.reviewed", "answer_conflict", conflict_id, {
        "trace_id": trace_id,
        "disposition": update.disposition,
        "has_review_note": bool(review_note),
    })
    session.commit()
    return ConflictResponse(**finding)


@app.delete("/api/v1/history/{trace_id}", status_code=204, tags=["Answers"])
def delete_answer_history(trace_id: str, session: SessionDep, principal: PrincipalDep) -> Response:
    if not settings.store_answer_traces:
        raise HTTPException(status_code=404, detail="Answer history is disabled.")
    trace = session.scalar(select(AnswerTrace).where(
        AnswerTrace.id == trace_id,
        AnswerTrace.organization_id == principal.organization_id,
        AnswerTrace.subject == principal.subject,
    ))
    if trace is None:
        raise HTTPException(status_code=404, detail="Answer history entry not found.")
    session.delete(trace)
    session.commit()
    return Response(status_code=204)


@app.delete("/api/v1/history", status_code=204, tags=["Answers"])
def clear_answer_history(session: SessionDep, principal: PrincipalDep) -> Response:
    if not settings.store_answer_traces:
        raise HTTPException(status_code=404, detail="Answer history is disabled.")
    session.execute(delete(AnswerTrace).where(
        AnswerTrace.organization_id == principal.organization_id,
        AnswerTrace.subject == principal.subject,
    ))
    session.commit()
    return Response(status_code=204)


@app.get("/api/v1/documents", response_model=list[DocumentResponse], tags=["Documents"])
def list_documents(session: SessionDep, principal: PrincipalDep) -> list[DocumentResponse]:
    documents = session.scalars(
        select(Document)
        .where(Document.organization_id == principal.organization_id)
        .order_by(Document.updated_at.desc())
    ).all()
    return [_document_response(document) for document in documents if _can_access(document, principal)]


@app.get("/api/v1/documents/{document_id}", response_model=DocumentDetailResponse, tags=["Documents"])
def get_document(document_id: str, session: SessionDep, principal: PrincipalDep) -> DocumentDetailResponse:
    document = session.scalar(
        select(Document)
        .where(
            Document.id == document_id,
            Document.organization_id == principal.organization_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if not _can_access(document, principal):
        raise HTTPException(status_code=404, detail="Document not found.")
    base = _document_response(document)
    return DocumentDetailResponse(**base.model_dump(), versions=[_version_response(v) for v in document.versions])


@app.get("/api/v1/documents/{document_id}/changes", response_model=DocumentChangesResponse, tags=["Documents"])
def get_document_changes(document_id: str, session: SessionDep, principal: PrincipalDep) -> DocumentChangesResponse:
    document = session.scalar(
        select(Document)
        .where(Document.id == document_id, Document.organization_id == principal.organization_id)
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if not _can_access(document, principal):
        raise HTTPException(status_code=404, detail="Document not found.")
    parsed = [version for version in document.versions if version.parsed_path and Path(version.parsed_path).is_file()]
    if len(parsed) < 2:
        raise HTTPException(status_code=409, detail="Two parsed versions are required to compare changes.")
    previous, current = parsed[-2:]
    old_text = Path(previous.parsed_path).read_text(encoding="utf-8").splitlines()
    new_text = Path(current.parsed_path).read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(
        old_text, new_text,
        fromfile=f"{document.original_filename} v{previous.version_number}",
        tofile=f"{document.original_filename} v{current.version_number}",
        lineterm="", n=2,
    ))
    unified_diff = "\n".join(diff)
    if len(unified_diff) > 24000:
        unified_diff = unified_diff[:24000] + "\n… diff truncated at 24,000 characters"
    additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    return DocumentChangesResponse(
        document_id=document.id,
        document_title=document.title,
        from_version=previous.version_number,
        to_version=current.version_number,
        additions=additions,
        deletions=deletions,
        unified_diff=unified_diff,
    )


@app.get(
    "/api/v1/documents/{document_id}/relationships",
    response_model=list[RelationshipResponse],
    tags=["Documents"],
)
def list_document_relationships(
    document_id: str,
    session: SessionDep,
    principal: PrincipalDep,
) -> list[RelationshipResponse]:
    document = session.scalar(select(Document).where(
        Document.id == document_id,
        Document.organization_id == principal.organization_id,
    ))
    if document is None or not _can_access(document, principal):
        raise HTTPException(status_code=404, detail="Document not found.")
    relationships = session.scalars(select(DocumentRelationship).where(
        DocumentRelationship.organization_id == principal.organization_id,
        or_(
            DocumentRelationship.source_document_id == document.id,
            DocumentRelationship.target_document_id == document.id,
        ),
    ).order_by(DocumentRelationship.created_at.desc())).all()
    linked_ids = {
        relationship.target_document_id if relationship.source_document_id == document.id
        else relationship.source_document_id
        for relationship in relationships
    }
    linked_documents = session.scalars(select(Document).where(
        Document.organization_id == principal.organization_id,
        Document.id.in_(linked_ids),
    )).all() if linked_ids else []
    accessible = {item.id: item for item in linked_documents if _can_access(item, principal)}
    result = []
    for relationship in relationships:
        source = document if relationship.source_document_id == document.id else accessible.get(relationship.source_document_id)
        target = document if relationship.target_document_id == document.id else accessible.get(relationship.target_document_id)
        if source is None or target is None:
            continue
        result.append(RelationshipResponse(
            id=relationship.id,
            source_document_id=source.id,
            source_document_title=source.title,
            target_document_id=target.id,
            target_document_title=target.title,
            relationship_type=relationship.relationship_type,
            created_at=relationship.created_at,
        ))
    return result


@app.post(
    "/api/v1/documents/{document_id}/relationships",
    response_model=RelationshipResponse,
    status_code=201,
    tags=["Documents"],
)
def create_document_relationship(
    document_id: str,
    payload: RelationshipCreate,
    session: SessionDep,
    principal: PrincipalDep,
) -> RelationshipResponse:
    if "sourcebound_admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="Workspace administrator role required.")
    source = session.scalar(select(Document).where(
        Document.id == document_id,
        Document.organization_id == principal.organization_id,
    ))
    target = session.scalar(select(Document).where(
        Document.id == payload.target_document_id,
        Document.organization_id == principal.organization_id,
    ))
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="Document not found in this organization.")
    if source.id == target.id:
        raise HTTPException(status_code=422, detail="A document cannot be related to itself.")
    relationship = DocumentRelationship(
        organization_id=principal.organization_id,
        source_document_id=source.id,
        target_document_id=target.id,
        relationship_type=payload.relationship_type,
        created_by=principal.subject,
    )
    session.add(relationship)
    _record_audit_event(session, principal, "document.relationship.created", "document", source.id, {
        "target_document_id": target.id,
        "relationship_type": payload.relationship_type,
    })
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="That document relationship already exists.") from exc
    return RelationshipResponse(
        id=relationship.id,
        source_document_id=source.id,
        source_document_title=source.title,
        target_document_id=target.id,
        target_document_title=target.title,
        relationship_type=relationship.relationship_type,
        created_at=relationship.created_at,
    )


@app.delete(
    "/api/v1/documents/{document_id}/relationships/{relationship_id}",
    status_code=204,
    tags=["Documents"],
)
def delete_document_relationship(
    document_id: str,
    relationship_id: str,
    session: SessionDep,
    principal: PrincipalDep,
) -> Response:
    if "sourcebound_admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="Workspace administrator role required.")
    document = session.scalar(select(Document).where(
        Document.id == document_id,
        Document.organization_id == principal.organization_id,
    ))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    relationship = session.scalar(select(DocumentRelationship).where(
        DocumentRelationship.id == relationship_id,
        DocumentRelationship.organization_id == principal.organization_id,
        or_(
            DocumentRelationship.source_document_id == document.id,
            DocumentRelationship.target_document_id == document.id,
        ),
    ))
    if relationship is None:
        raise HTTPException(status_code=404, detail="Document relationship not found.")
    _record_audit_event(session, principal, "document.relationship.deleted", "document", document.id, {
        "relationship_id": relationship.id,
        "relationship_type": relationship.relationship_type,
    })
    session.delete(relationship)
    session.commit()
    return Response(status_code=204)


@app.delete("/api/v1/documents/{document_id}", status_code=204, tags=["Documents"])
def delete_document(document_id: str, session: SessionDep, principal: PrincipalDep) -> Response:
    """Permanently remove a source, its versions, stored files, and all vector points."""
    if "sourcebound_admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="Workspace administrator role required.")
    document = session.scalar(select(Document).where(
        Document.id == document_id,
        Document.organization_id == principal.organization_id,
    ).with_for_update())
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if any(version.status in {"queued", "processing", "indexing"} for version in document.versions):
        raise HTTPException(status_code=409, detail="Wait for all document versions to finish processing before deleting this source.")

    usage = _lock_organization_usage(session, principal.organization_id)
    removed_bytes = sum(version.byte_size for version in document.versions)

    try:
        document_directory = str(UUID(document.id))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Stored document identifier is invalid.") from exc
    organization_directory = principal.organization_id
    if organization_directory in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", organization_directory):
        raise HTTPException(status_code=500, detail="Stored organization identifier is invalid.")

    storage_root = settings.storage_root.resolve()
    source_directory = (storage_root / organization_directory / document_directory).resolve()
    try:
        source_directory.relative_to(storage_root)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Stored document path is outside the configured storage root.") from exc
    if source_directory == storage_root:
        raise HTTPException(status_code=500, detail="Stored document path is invalid.")

    try:
        delete_document_points(document.id)
    except Exception as exc:
        logger.exception("Could not remove indexed data for document %s", document.id)
        raise HTTPException(status_code=503, detail="Search index data could not be removed. The document record is unchanged; retry after search is available.") from exc
    try:
        shutil.rmtree(source_directory, ignore_errors=False)
    except FileNotFoundError:
        # The source row remains authoritative even if its local files were removed manually.
        pass
    except Exception as exc:
        logger.exception("Could not remove stored files for document %s", document.id)
        raise HTTPException(status_code=503, detail="Stored files could not be removed. The document record is unchanged; retry after storage is available.") from exc

    affected_traces = session.scalars(select(AnswerTrace).where(
        AnswerTrace.organization_id == principal.organization_id,
    )).all()
    deleted_trace_count = 0
    for trace in affected_traces:
        if any(isinstance(row, dict) and row.get("document_id") == document.id for row in (trace.citations or [])):
            session.delete(trace)
            deleted_trace_count += 1
    relationship_delete = session.execute(delete(DocumentRelationship).where(
        DocumentRelationship.organization_id == principal.organization_id,
        or_(DocumentRelationship.source_document_id == document.id, DocumentRelationship.target_document_id == document.id),
    ))
    _record_audit_event(session, principal, "document.deleted", "document", document.id, {
        "version_count": len(document.versions),
        "storage_bytes_released": removed_bytes,
        "answer_traces_deleted": deleted_trace_count,
        "relationships_deleted": relationship_delete.rowcount or 0,
    })
    usage.document_count = max(0, usage.document_count - 1)
    usage.storage_bytes = max(0, usage.storage_bytes - removed_bytes)
    usage.updated_at = datetime.now(UTC)
    session.delete(document)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("Source data was removed but the database transaction failed for document %s", document.id)
        raise HTTPException(status_code=503, detail="Source files and index points were removed, but the document record could not be deleted. Retry the deletion.") from exc
    return Response(status_code=204)


@app.put("/api/v1/documents/{document_id}/access", response_model=DocumentResponse, tags=["Documents"])
def update_document_access(
    document_id: str,
    update: AccessUpdate,
    session: SessionDep,
    principal: PrincipalDep,
) -> DocumentResponse:
    if "sourcebound_admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="Workspace administrator role required.")
    document = session.scalar(select(Document).where(
        Document.id == document_id,
        Document.organization_id == principal.organization_id,
    ).with_for_update())
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    previous_groups = document.access_groups
    groups = sorted(set(group.strip() for group in (update.access_groups or []) if group.strip()))
    if any(len(group) > 255 for group in groups):
        raise HTTPException(status_code=422, detail="Group names are limited to 255 characters.")
    if update.access_groups is not None and groups:
        registered = set(session.scalars(select(OrganizationGroup.name).where(
            OrganizationGroup.organization_id == principal.organization_id,
            OrganizationGroup.name.in_(groups),
        ).with_for_update()).all())
        unknown = sorted(set(groups) - registered)
        if unknown:
            raise HTTPException(status_code=422, detail="Register each identity group before assigning it to a document policy.")
    document.access_groups = groups if update.access_groups is not None else None
    _record_audit_event(session, principal, "document.access.updated", "document", document.id, {
        "previous_groups": previous_groups,
        "access_groups": document.access_groups,
    })
    session.commit()
    session.refresh(document)
    try:
        update_document_visibility(document.id, document.access_groups)
    except Exception as exc:
        # Database ACLs and the allowed-document list still enforce restrictions if the index update is delayed.
        logger.exception("Could not synchronize vector ACL for document %s", document.id)
        raise HTTPException(status_code=503, detail="Document access was saved, but the search index is not synchronized yet.") from exc
    return _document_response(document)


@app.post("/api/v1/documents", response_model=DocumentDetailResponse, status_code=202, tags=["Documents"])
async def upload_document(
    background_tasks: BackgroundTasks,
    request: Request,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    file: Annotated[UploadFile, File(description="PDF file to add to the knowledge base")],
) -> DocumentDetailResponse:
    enforce_rate_limit(
        session, request, response, principal,
        policy="ingestion", limit=settings.upload_rate_limit_per_minute,
    )
    enforce_rate_limit(
        session, request, response, principal,
        policy="ingestion", limit=settings.organization_upload_rate_limit_per_minute,
        scope="organization",
    )
    filename = _safe_filename(file.filename)
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="Upload a PDF file. Other document formats are planned.")

    usage = _lock_organization_usage(session, principal.organization_id)
    if usage.document_count >= settings.organization_max_documents:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Organization document quota is full. Remove a source or ask an administrator to raise the configured limit.",
        )

    if settings.auth_mode == "oidc":
        _sync_identity_groups(session, principal)

    document = Document(
        organization_id=principal.organization_id,
        access_groups=(list(principal.groups) if settings.auth_mode == "oidc" else None),
        title=Path(filename).stem,
        original_filename=filename,
        latest_version=1,
    )
    session.add(document)
    session.flush()
    destination = settings.storage_root / principal.organization_id / document.id / "v1.pdf"
    sha256, byte_size = await _store_pdf(file, destination)
    try:
        _check_storage_quota(usage, byte_size)
        version = DocumentVersion(
            document_id=document.id,
            version_number=1,
            sha256=sha256,
            byte_size=byte_size,
            storage_path=str(destination.resolve()),
            status="queued",
        )
        session.add(version)
        document.versions.append(version)
        document.updated_at = datetime.now(UTC)
        usage.document_count += 1
        usage.storage_bytes += byte_size
        usage.updated_at = datetime.now(UTC)
        session.flush()
        _record_audit_event(session, principal, "document.created", "document", document.id, {
            "version_id": version.id,
            "version_number": 1,
            "byte_size": byte_size,
            "sha256": sha256,
        })
        session.commit()
    except Exception:
        session.rollback()
        destination.unlink(missing_ok=True)
        raise
    session.refresh(document)

    try:
        _schedule_parse(version.id, background_tasks)
    except Exception as exc:
        version.status = "failed"
        version.error_message = f"Could not queue document processing: {exc}"[:2000]
        session.commit()
        raise HTTPException(status_code=503, detail="Document saved, but processing could not be queued.") from exc

    return DocumentDetailResponse(**_document_response(document).model_dump(), versions=[_version_response(version)])


@app.post(
    "/api/v1/documents/{document_id}/versions",
    response_model=VersionResponse,
    status_code=202,
    tags=["Documents"],
)
async def upload_document_version(
    document_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    file: Annotated[UploadFile, File(description="Updated PDF version")],
) -> VersionResponse:
    enforce_rate_limit(
        session, request, response, principal,
        policy="ingestion", limit=settings.upload_rate_limit_per_minute,
    )
    enforce_rate_limit(
        session, request, response, principal,
        policy="ingestion", limit=settings.organization_upload_rate_limit_per_minute,
        scope="organization",
    )
    document = session.scalar(
        select(Document)
        .where(
            Document.id == document_id,
            Document.organization_id == principal.organization_id,
        )
        .with_for_update()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if not _can_access(document, principal):
        raise HTTPException(status_code=404, detail="Document not found.")
    usage = _lock_organization_usage(session, principal.organization_id)

    filename = _safe_filename(file.filename)
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="Upload a PDF file. Other document formats are planned.")
    next_version = document.latest_version + 1
    destination = settings.storage_root / principal.organization_id / document.id / f"v{next_version}.pdf"
    sha256, byte_size = await _store_pdf(file, destination)
    try:
        duplicate = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.sha256 == sha256,
            )
        )
        if duplicate is not None:
            destination.unlink(missing_ok=True)
            return _version_response(duplicate)

        _check_storage_quota(usage, byte_size)
        version = DocumentVersion(
            document_id=document.id,
            version_number=next_version,
            sha256=sha256,
            byte_size=byte_size,
            storage_path=str(destination.resolve()),
            status="queued",
        )
        document.latest_version = next_version
        document.original_filename = filename
        document.updated_at = datetime.now(UTC)
        usage.storage_bytes += byte_size
        usage.updated_at = datetime.now(UTC)
        session.add(version)
        session.flush()
        _record_audit_event(session, principal, "document.version.uploaded", "document", document.id, {
            "version_id": version.id,
            "version_number": next_version,
            "byte_size": byte_size,
            "sha256": sha256,
        })
        session.commit()
    except Exception:
        session.rollback()
        destination.unlink(missing_ok=True)
        raise
    session.refresh(version)

    try:
        _schedule_parse(version.id, background_tasks)
    except Exception as exc:
        version.status = "failed"
        version.error_message = f"Could not queue document processing: {exc}"[:2000]
        session.commit()
        raise HTTPException(status_code=503, detail="Version saved, but processing could not be queued.") from exc

    return _version_response(version)


@app.post(
    "/api/v1/documents/{document_id}/reprocess",
    response_model=VersionResponse,
    status_code=202,
    tags=["Documents"],
)
def reprocess_document_version(
    document_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
) -> VersionResponse:
    if "sourcebound_admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="Workspace administrator role required.")
    enforce_rate_limit(
        session, request, response, principal,
        policy="reprocess", limit=settings.reprocess_rate_limit_per_minute,
    )
    enforce_rate_limit(
        session, request, response, principal,
        policy="reprocess", limit=settings.organization_reprocess_rate_limit_per_minute,
        scope="organization",
    )
    document = session.scalar(select(Document).where(
        Document.id == document_id,
        Document.organization_id == principal.organization_id,
    ).with_for_update())
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    version = document.versions[-1] if document.versions else None
    if version is None:
        raise HTTPException(status_code=404, detail="Document version not found.")
    if version.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed document versions can be reprocessed.")
    version.status = "queued"
    version.error_message = None
    version.processed_at = None
    _record_audit_event(session, principal, "document.version.reprocess_requested", "document", document.id, {
        "version_id": version.id,
        "version_number": version.version_number,
    })
    session.commit()
    session.refresh(version)
    try:
        _schedule_parse(version.id, background_tasks)
    except Exception as exc:
        version.status = "failed"
        version.error_message = f"Could not queue document processing: {type(exc).__name__}"[:2000]
        session.commit()
        raise HTTPException(status_code=503, detail="Document version could not be queued for processing.") from exc
    return _version_response(version)
