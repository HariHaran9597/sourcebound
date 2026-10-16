"""Persist evidence-linked potential conflict findings with answer traces."""

from alembic import op
import sqlalchemy as sa

revision = "0005_answer_trace_conflicts"
down_revision = "0004_answer_trace_reranker"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "answer_traces",
        sa.Column("conflicts", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("answer_traces", "conflicts")
