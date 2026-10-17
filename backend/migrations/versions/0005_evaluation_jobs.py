"""durable evaluation job submissions

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_job_queue_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("queued_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_job_queue_singleton_id"),
        sa.CheckConstraint("queued_count >= 0 AND queued_count <= 100", name="ck_job_queue_count"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(
        sa.table(
            "evaluation_job_queue_state",
            sa.column("id", sa.Integer()),
            sa.column("queued_count", sa.Integer()),
        ),
        [{"id": 1, "queued_count": 0}],
    )

    op.create_table(
        "evaluation_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("scoring_input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("knowledge_base_hash", sa.String(length=64), nullable=True),
        sa.Column("completed_count", sa.Integer(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("retry_of_job_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_evaluation_job_status",
        ),
        sa.CheckConstraint("completed_count >= 0", name="ck_evaluation_job_completed_count"),
        sa.CheckConstraint("total_count >= 0", name="ck_evaluation_job_total_count"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_evaluation_job_attempt_count"),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["retry_of_job_id"], ["evaluation_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_evaluation_job_idempotency_key"),
        sa.UniqueConstraint("run_id", name="uq_evaluation_jobs_run_id"),
    )
    op.create_index(
        "ix_evaluation_jobs_status_created_id",
        "evaluation_jobs",
        ["status", "created_at", "id"],
    )
    op.create_index("ix_evaluation_jobs_dataset_id", "evaluation_jobs", ["dataset_id"])
    op.create_index("ix_evaluation_jobs_retry_of", "evaluation_jobs", ["retry_of_job_id"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_jobs_retry_of", table_name="evaluation_jobs")
    op.drop_index("ix_evaluation_jobs_dataset_id", table_name="evaluation_jobs")
    op.drop_index("ix_evaluation_jobs_status_created_id", table_name="evaluation_jobs")
    op.drop_table("evaluation_jobs")
    op.drop_table("evaluation_job_queue_state")
