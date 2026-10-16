"""Record the reranker used for each answer trace."""

from alembic import op
import sqlalchemy as sa

revision = "0004_answer_trace_reranker"
down_revision = "0003_audit_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("answer_traces", sa.Column("reranker_model", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("answer_traces", "reranker_model")
