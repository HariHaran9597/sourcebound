"""Add reusable organization-scoped access groups and import existing ACL values."""

from datetime import UTC, datetime
from uuid import uuid4
import json

from alembic import op
import sqlalchemy as sa

revision = "0009_organization_groups"
down_revision = "0008_document_relationships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization_groups",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "name"),
    )
    op.create_index("ix_organization_groups_organization_id", "organization_groups", ["organization_id"])

    documents = sa.table(
        "documents",
        sa.column("organization_id", sa.String(length=36)),
        sa.column("access_groups", sa.JSON()),
    )
    groups = sa.table(
        "organization_groups",
        sa.column("id", sa.String(length=36)),
        sa.column("organization_id", sa.String(length=36)),
        sa.column("name", sa.String(length=255)),
        sa.column("created_by", sa.String(length=255)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    connection = op.get_bind()
    existing: set[tuple[str, str]] = set()
    for organization_id, access_groups in connection.execute(
        sa.select(documents.c.organization_id, documents.c.access_groups).where(documents.c.access_groups.is_not(None))
    ):
        if isinstance(access_groups, str):
            try:
                access_groups = json.loads(access_groups)
            except json.JSONDecodeError:
                continue
        if not isinstance(access_groups, list):
            continue
        for name in access_groups:
            if isinstance(name, str) and name.strip() and len(name.strip()) <= 255:
                existing.add((organization_id, name.strip()))
    now = datetime.now(UTC)
    if existing:
        connection.execute(groups.insert(), [
            {
                "id": str(uuid4()),
                "organization_id": organization_id,
                "name": name,
                "created_by": "migration:0009",
                "created_at": now,
            }
            for organization_id, name in sorted(existing)
        ])


def downgrade() -> None:
    op.drop_index("ix_organization_groups_organization_id", table_name="organization_groups")
    op.drop_table("organization_groups")
