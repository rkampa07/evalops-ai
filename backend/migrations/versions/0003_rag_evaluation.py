"""RAG evaluation: knowledge-base passages, RAG case fields, retrieval and generation
metadata, model-based grades

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13

Purely additive. Existing rows keep their values; new columns default to empty/NULL, so
existing test-case definition hashes are unchanged (see engine/versioning.py).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "passages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("passage_id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "passage_id", name="uq_passage_dataset_pid"),
    )
    with op.batch_alter_table("passages") as batch:
        batch.create_index(batch.f("ix_passages_dataset_id"), ["dataset_id"], unique=False)

    with op.batch_alter_table("test_cases") as batch:
        batch.add_column(
            sa.Column("relevant_passage_ids", sa.JSON(), server_default="[]", nullable=False)
        )
        batch.add_column(
            sa.Column("expect_abstain", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.add_column(sa.Column("knowledge_base_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("generation", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("model_graded", sa.JSON(), nullable=True))
    with op.batch_alter_table("evaluation_results") as batch:
        batch.add_column(sa.Column("retrieved_passage_ids", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("generation", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("model_grades", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("evaluation_results") as batch:
        batch.drop_column("model_grades")
        batch.drop_column("generation")
        batch.drop_column("retrieved_passage_ids")
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.drop_column("model_graded")
        batch.drop_column("generation")
        batch.drop_column("knowledge_base_hash")
    with op.batch_alter_table("test_cases") as batch:
        batch.drop_column("expect_abstain")
        batch.drop_column("relevant_passage_ids")
    with op.batch_alter_table("passages") as batch:
        batch.drop_index(batch.f("ix_passages_dataset_id"))
    op.drop_table("passages")
