from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import BigInteger, CheckConstraint, JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sourcebound_api.db import Base


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), index=True)
    access_groups: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=None)
    title: Mapped[str] = mapped_column(String(500))
    original_filename: Mapped[str] = mapped_column(String(500))
    latest_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentVersion.version_number"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(Text)
    parsed_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parser_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    document: Mapped[Document] = relationship(back_populates="versions")


class AnswerTrace(Base):
    """A user-scoped snapshot of one answer and the exact evidence shown with it."""

    __tablename__ = "answer_traces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), index=True)
    subject: Mapped[str] = mapped_column(String(255), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict]] = mapped_column(JSON)
    conflicts: Mapped[list[dict]] = mapped_column(JSON, default=list)
    abstained: Mapped[bool] = mapped_column(default=False)
    model_name: Mapped[str] = mapped_column(String(200))
    embedding_model: Mapped[str] = mapped_column(String(200))
    reranker_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    retrieval_version: Mapped[str] = mapped_column(String(100))
    retrieval_scope: Mapped[str] = mapped_column(String(32), default="latest")
    prompt_version: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AuditEvent(Base):
    """Append-only record of security-relevant workspace changes."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), index=True)
    actor_subject: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(255), index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class RateLimitBucket(Base):
    """Short-lived aggregate counters keyed by a one-way hash, never raw identities."""

    __tablename__ = "rate_limit_buckets"

    bucket_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DocumentRelationship(Base):
    """An administrator-curated, directed link between two same-organization sources."""

    __tablename__ = "document_relationships"
    __table_args__ = (
        UniqueConstraint("source_document_id", "target_document_id", "relationship_type"),
        CheckConstraint("source_document_id <> target_document_id", name="ck_document_relationship_not_self"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), index=True)
    source_document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    target_document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    relationship_type: Mapped[str] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OrganizationGroup(Base):
    """Organization-scoped reusable names used by document access policies."""

    __tablename__ = "organization_groups"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OrganizationUsage(Base):
    """Locked, transactionally maintained source counts for organization quotas."""

    __tablename__ = "organization_usage"

    organization_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    storage_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
