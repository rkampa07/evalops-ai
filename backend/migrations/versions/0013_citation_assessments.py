"""persist workspace-scoped frozen citation assessments

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Composite ownership FKs below need unique target keys. `id` is already a PK, but SQLite
    # requires the exact composite referenced columns to be covered by a unique index.
    op.create_index("uq_result_id_run", "evaluation_results", ["id", "run_id"], unique=True)
    op.create_table(
        "citation_assessment_queue_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("queued_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("id = 1", name="ck_citation_assessment_queue_singleton"),
        sa.CheckConstraint(
            "queued_count >= 0 AND queued_count <= 50", name="ck_citation_assessment_queue_count"
        ),
    )
    op.execute(
        sa.text("INSERT INTO citation_assessment_queue_state (id, queued_count) VALUES (1, 0)")
    )
    op.create_table(
        "citation_assessment_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evaluator_kind", sa.String(20), nullable=False, server_default="citation"),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "submitted_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("submitter_key", sa.String(100), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("result_id", sa.Integer(), nullable=False),
        sa.Column(
            "dataset_id",
            sa.Integer(),
            sa.ForeignKey("datasets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("case_key", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("citation_expected", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("knowledge_base_hash", sa.String(64), nullable=True),
        sa.Column("predictor_sha256", sa.String(64), nullable=False),
        sa.Column("runner_sha256", sa.String(64), nullable=False),
        sa.Column("config_digest", sa.String(64), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.UniqueConstraint(
            "workspace_id",
            "submitter_key",
            "idempotency_key",
            name="uq_citation_assessment_idempotency",
        ),
        sa.UniqueConstraint("id", "workspace_id", name="uq_citation_assessment_id_workspace"),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["evaluation_runs.id", "evaluation_runs.workspace_id"],
            name="fk_citation_assessment_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_id", "run_id"],
            ["evaluation_results.id", "evaluation_results.run_id"],
            name="fk_citation_assessment_result_run",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("evaluator_kind = 'citation'", name="ck_citation_assessment_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_citation_assessment_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 3", name="ck_citation_assessment_attempts"
        ),
    )
    op.create_index(
        "ix_citation_assessment_queue", "citation_assessment_jobs", ["status", "created_at"]
    )
    op.create_index(
        "ix_citation_assessment_workspace",
        "citation_assessment_jobs",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_citation_assessment_jobs_workspace_id", "citation_assessment_jobs", ["workspace_id"]
    )
    op.create_index(
        "ix_citation_assessment_jobs_submitted_by_user_id",
        "citation_assessment_jobs",
        ["submitted_by_user_id"],
    )
    op.create_table(
        "citation_assessment_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("result_id", sa.Integer(), nullable=False),
        sa.Column("case_key", sa.String(100), nullable=False),
        sa.Column("evaluator_version", sa.String(50), nullable=False),
        sa.Column("predictor_sha256", sa.String(64), nullable=False),
        sa.Column("runner_sha256", sa.String(64), nullable=False),
        sa.Column("config_digest", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("knowledge_base_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("per_citation", sa.JSON(), nullable=False),
        sa.Column("prediction_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", name="uq_citation_assessment_result_job"),
        sa.ForeignKeyConstraint(
            ["job_id", "workspace_id"],
            ["citation_assessment_jobs.id", "citation_assessment_jobs.workspace_id"],
            name="fk_citation_assessment_result_job_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["evaluation_runs.id", "evaluation_runs.workspace_id"],
            name="fk_citation_result_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_id", "run_id"],
            ["evaluation_results.id", "evaluation_results.run_id"],
            name="fk_citation_result_result_run",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_citation_assessment_result_workspace",
        "citation_assessment_results",
        ["workspace_id", "created_at"],
    )
    op.execute("""
        CREATE TRIGGER citation_assessment_results_no_update
        BEFORE UPDATE ON citation_assessment_results
        BEGIN SELECT RAISE(ABORT, 'citation assessment results are immutable'); END
    """)
    op.execute("""
        CREATE TRIGGER citation_assessment_results_no_delete
        BEFORE DELETE ON citation_assessment_results
        BEGIN SELECT RAISE(ABORT, 'citation assessment results are immutable'); END
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS citation_assessment_results_no_delete")
    op.execute("DROP TRIGGER IF EXISTS citation_assessment_results_no_update")
    op.drop_index(
        "ix_citation_assessment_result_workspace", table_name="citation_assessment_results"
    )
    op.drop_table("citation_assessment_results")
    op.drop_index(
        "ix_citation_assessment_jobs_submitted_by_user_id", table_name="citation_assessment_jobs"
    )
    op.drop_index("ix_citation_assessment_jobs_workspace_id", table_name="citation_assessment_jobs")
    op.drop_index("ix_citation_assessment_workspace", table_name="citation_assessment_jobs")
    op.drop_index("ix_citation_assessment_queue", table_name="citation_assessment_jobs")
    op.drop_table("citation_assessment_jobs")
    op.drop_table("citation_assessment_queue_state")
    op.drop_index("uq_result_id_run", table_name="evaluation_results")
