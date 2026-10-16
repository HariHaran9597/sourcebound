"""Store per-user answer history with immutable evidence snapshots."""

from alembic import op
import sqlalchemy as sa

revision = "0002_answer_traces"
down_revision = "0001_initial_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "answer_traces",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("abstained", sa.Boolean(), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.Column("retrieval_version", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_answer_traces_organization_id", "answer_traces", ["organization_id"])
    op.create_index("ix_answer_traces_subject", "answer_traces", ["subject"])
    op.create_index("ix_answer_traces_created_at", "answer_traces", ["created_at"])
    op.create_index("ix_answer_traces_expires_at", "answer_traces", ["expires_at"])


def downgrade() -> None:
    op.drop_table("answer_traces")
