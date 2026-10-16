"""Track organization document and source-storage usage for upload quotas."""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "0011_organization_usage_quotas"
down_revision = "0010_document_processing_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization_usage",
        sa.Column("organization_id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column("document_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("storage_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    documents = sa.table(
        "documents",
        sa.column("id", sa.String(length=36)),
        sa.column("organization_id", sa.String(length=36)),
    )
    versions = sa.table(
        "document_versions",
        sa.column("document_id", sa.String(length=36)),
        sa.column("byte_size", sa.Integer()),
    )
    usage = sa.table(
        "organization_usage",
        sa.column("organization_id", sa.String(length=36)),
        sa.column("document_count", sa.Integer()),
        sa.column("storage_bytes", sa.BigInteger()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(
            documents.c.organization_id,
            sa.func.count(sa.distinct(documents.c.id)),
            sa.func.coalesce(sa.func.sum(versions.c.byte_size), 0),
        )
        .select_from(documents.outerjoin(versions, versions.c.document_id == documents.c.id))
        .group_by(documents.c.organization_id)
    ).all()
    now = datetime.now(UTC)
    if rows:
        connection.execute(usage.insert(), [
            {
                "organization_id": organization_id,
                "document_count": document_count,
                "storage_bytes": storage_bytes,
                "updated_at": now,
            }
            for organization_id, document_count, storage_bytes in rows
        ])


def downgrade() -> None:
    op.drop_table("organization_usage")
