"""Persist processing attempts and latest-attempt start time."""

from alembic import op
import sqlalchemy as sa

revision = "0010_document_processing_metrics"
down_revision = "0009_organization_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("processing_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "document_versions",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "processing_started_at")
    op.drop_column("document_versions", "processing_attempts")
