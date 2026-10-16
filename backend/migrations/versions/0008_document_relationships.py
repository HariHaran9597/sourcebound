"""Add curated document-to-document relationships."""

from alembic import op
import sqlalchemy as sa

revision = "0008_document_relationships"
down_revision = "0007_retrieval_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_relationships",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_document_id", sa.String(length=36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship_type", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_document_id", "target_document_id", "relationship_type"),
        sa.CheckConstraint("source_document_id <> target_document_id", name="ck_document_relationship_not_self"),
    )
    op.create_index("ix_document_relationships_organization_id", "document_relationships", ["organization_id"])
    op.create_index("ix_document_relationships_source_document_id", "document_relationships", ["source_document_id"])
    op.create_index("ix_document_relationships_target_document_id", "document_relationships", ["target_document_id"])


def downgrade() -> None:
    op.drop_index("ix_document_relationships_target_document_id", table_name="document_relationships")
    op.drop_index("ix_document_relationships_source_document_id", table_name="document_relationships")
    op.drop_index("ix_document_relationships_organization_id", table_name="document_relationships")
    op.drop_table("document_relationships")
