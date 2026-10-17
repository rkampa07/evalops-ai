"""add durable frozen semantic claim assessments

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "assessment_id_sequence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("next_id", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_assessment_id_sequence_singleton"),
    )
    next_id = (
        bind.execute(
            sa.text("SELECT COALESCE(MAX(id), 0) + 1 FROM citation_assessment_jobs")
        ).scalar()
        or 1
    )
    op.execute(
        sa.text("INSERT INTO assessment_id_sequence (id, next_id) VALUES (1, :next_id)").bindparams(
            next_id=next_id
        )
    )
    op.create_table(
        "assessment_idempotency",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("submitter_key", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("evaluator_kind", sa.String(20), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "submitter_key", "idempotency_key", name="uq_assessment_idempotency"
        ),
        sa.CheckConstraint("evaluator_kind IN ('citation', 'claim')", name="ck_assessment_kind"),
    )
    op.execute(
        sa.text("""
        INSERT INTO assessment_idempotency
            (workspace_id, submitter_key, idempotency_key, evaluator_kind, request_hash,
             assessment_id)
        SELECT workspace_id, submitter_key, idempotency_key, 'citation', request_hash, id
        FROM citation_assessment_jobs
    """)
    )
    op.create_table(
        "claim_assessment_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evaluator_kind", sa.String(20), nullable=False, server_default="claim"),
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
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("knowledge_base_hash", sa.String(64), nullable=True),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("config_digest", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("model_revision", sa.String(64), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
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
            name="uq_claim_assessment_idempotency",
        ),
        sa.UniqueConstraint("id", "workspace_id", name="uq_claim_assessment_id_workspace"),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["evaluation_runs.id", "evaluation_runs.workspace_id"],
            name="fk_claim_assessment_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_id", "run_id"],
            ["evaluation_results.id", "evaluation_results.run_id"],
            name="fk_claim_assessment_result_run",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("evaluator_kind = 'claim'", name="ck_claim_assessment_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_claim_assessment_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 3", name="ck_claim_assessment_attempts"
        ),
    )
    op.create_index("ix_claim_assessment_queue", "claim_assessment_jobs", ["status", "created_at"])
    op.create_index(
        "ix_claim_assessment_workspace", "claim_assessment_jobs", ["workspace_id", "created_at"]
    )
    op.create_table(
        "claim_assessment_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("result_id", sa.Integer(), nullable=False),
        sa.Column("case_key", sa.String(100), nullable=False),
        sa.Column("evaluator_version", sa.String(50), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("config_digest", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("model_revision", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("knowledge_base_hash", sa.String(64), nullable=True),
        sa.Column("output_status", sa.String(50), nullable=False),
        sa.Column("claims", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", name="uq_claim_assessment_result_job"),
        sa.ForeignKeyConstraint(
            ["job_id", "workspace_id"],
            ["claim_assessment_jobs.id", "claim_assessment_jobs.workspace_id"],
            name="fk_claim_result_job_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["evaluation_runs.id", "evaluation_runs.workspace_id"],
            name="fk_claim_result_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_id", "run_id"],
            ["evaluation_results.id", "evaluation_results.run_id"],
            name="fk_claim_result_result_run",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_claim_assessment_result_workspace",
        "claim_assessment_results",
        ["workspace_id", "created_at"],
    )
    op.execute("""
        CREATE TRIGGER claim_assessment_results_no_update
        BEFORE UPDATE ON claim_assessment_results
        BEGIN SELECT RAISE(ABORT, 'claim assessment results are immutable'); END
    """)
    op.execute("""
        CREATE TRIGGER claim_assessment_results_no_delete
        BEFORE DELETE ON claim_assessment_results
        BEGIN SELECT RAISE(ABORT, 'claim assessment results are immutable'); END
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS claim_assessment_results_no_delete")
    op.execute("DROP TRIGGER IF EXISTS claim_assessment_results_no_update")
    op.drop_index("ix_claim_assessment_result_workspace", table_name="claim_assessment_results")
    op.drop_table("claim_assessment_results")
    op.drop_index("ix_claim_assessment_workspace", table_name="claim_assessment_jobs")
    op.drop_index("ix_claim_assessment_queue", table_name="claim_assessment_jobs")
    op.drop_table("claim_assessment_jobs")
    op.drop_table("assessment_idempotency")
    op.drop_table("assessment_id_sequence")
