"""Live chat playground: chat sessions and messages with execution traces

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14

Purely additive: two new tables; no existing table, row or methodology is changed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("chat_sessions") as batch:
        batch.create_index(batch.f("ix_chat_sessions_dataset_id"), ["dataset_id"], unique=False)
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("trace", sa.JSON(), nullable=True),
        sa.Column("saved_test_case_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["saved_test_case_id"], ["test_cases.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("chat_messages") as batch:
        batch.create_index(batch.f("ix_chat_messages_session_id"), ["session_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("chat_messages") as batch:
        batch.drop_index(batch.f("ix_chat_messages_session_id"))
    op.drop_table("chat_messages")
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_index(batch.f("ix_chat_sessions_dataset_id"))
    op.drop_table("chat_sessions")
