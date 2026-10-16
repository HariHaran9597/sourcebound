"""Preserve the selected version scope in answer provenance."""

from alembic import op
import sqlalchemy as sa

revision = "0007_retrieval_scope"
down_revision = "0006_rate_limit_buckets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "answer_traces",
        sa.Column("retrieval_scope", sa.String(length=32), nullable=False, server_default="latest"),
    )


def downgrade() -> None:
    op.drop_column("answer_traces", "retrieval_scope")
