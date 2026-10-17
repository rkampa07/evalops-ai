"""add workspace ownership foundations and backfill the reserved legacy workspace

Revision ID: 0007
Revises: 0006
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_WORKSPACE_ID = 1


def _ownership_problems() -> list[str]:
    """Return legacy references that cannot safely inherit a workspace."""
    conn = op.get_bind()
    checks = {
        "test_cases without datasets": """
            SELECT c.id FROM test_cases c LEFT JOIN datasets d ON d.id = c.dataset_id
            WHERE d.id IS NULL ORDER BY c.id LIMIT 20
        """,
        "passages without datasets": """
            SELECT p.id FROM passages p LEFT JOIN datasets d ON d.id = p.dataset_id
            WHERE d.id IS NULL ORDER BY p.id LIMIT 20
        """,
        "runs without datasets": """
            SELECT r.id FROM evaluation_runs r LEFT JOIN datasets d ON d.id = r.dataset_id
            WHERE d.id IS NULL ORDER BY r.id LIMIT 20
        """,
        "results without runs": """
            SELECT x.id FROM evaluation_results x
            LEFT JOIN evaluation_runs r ON r.id = x.run_id
            WHERE r.id IS NULL ORDER BY x.id LIMIT 20
        """,
        "results with missing cases": """
            SELECT x.id FROM evaluation_results x
            LEFT JOIN test_cases c ON c.id = x.test_case_id
            WHERE x.test_case_id IS NOT NULL AND c.id IS NULL ORDER BY x.id LIMIT 20
        """,
        "results whose case belongs to another dataset": """
            SELECT x.id FROM evaluation_results x
            JOIN evaluation_runs r ON r.id = x.run_id
            JOIN test_cases c ON c.id = x.test_case_id
            WHERE x.test_case_id IS NOT NULL AND c.dataset_id != r.dataset_id
            ORDER BY x.id LIMIT 20
        """,
        "jobs without datasets": """
            SELECT j.id FROM evaluation_jobs j
            LEFT JOIN datasets d ON d.id = j.dataset_id
            WHERE d.id IS NULL ORDER BY j.id LIMIT 20
        """,
        "jobs linked to runs from another dataset": """
            SELECT j.id FROM evaluation_jobs j
            JOIN evaluation_runs r ON r.id = j.run_id
            WHERE j.run_id IS NOT NULL AND r.dataset_id != j.dataset_id
            ORDER BY j.id LIMIT 20
        """,
        "jobs with missing retry parents": """
            SELECT j.id FROM evaluation_jobs j
            LEFT JOIN evaluation_jobs parent ON parent.id = j.retry_of_job_id
            WHERE j.retry_of_job_id IS NOT NULL AND parent.id IS NULL
            ORDER BY j.id LIMIT 20
        """,
        "jobs whose retry parent uses another dataset": """
            SELECT j.id FROM evaluation_jobs j
            JOIN evaluation_jobs parent ON parent.id = j.retry_of_job_id
            WHERE j.retry_of_job_id IS NOT NULL AND parent.dataset_id != j.dataset_id
            ORDER BY j.id LIMIT 20
        """,
        "chat sessions without datasets": """
            SELECT s.id FROM chat_sessions s LEFT JOIN datasets d ON d.id = s.dataset_id
            WHERE d.id IS NULL ORDER BY s.id LIMIT 20
        """,
        "chat messages without sessions": """
            SELECT m.id FROM chat_messages m LEFT JOIN chat_sessions s ON s.id = m.session_id
            WHERE s.id IS NULL ORDER BY m.id LIMIT 20
        """,
        "chat messages with missing saved cases": """
            SELECT m.id FROM chat_messages m LEFT JOIN test_cases c ON c.id = m.saved_test_case_id
            WHERE m.saved_test_case_id IS NOT NULL AND c.id IS NULL ORDER BY m.id LIMIT 20
        """,
        "chat messages whose saved case belongs to another dataset": """
            SELECT m.id FROM chat_messages m
            JOIN chat_sessions s ON s.id = m.session_id
            JOIN test_cases c ON c.id = m.saved_test_case_id
            WHERE m.saved_test_case_id IS NOT NULL AND c.dataset_id != s.dataset_id
            ORDER BY m.id LIMIT 20
        """,
    }
    problems = []
    for description, query in checks.items():
        ids = [row[0] for row in conn.execute(sa.text(query)).all()]
        if ids:
            problems.append(f"{description}: IDs {ids}")
    payload_mismatches = []
    for row in conn.execute(sa.text("SELECT id, dataset_id, request_payload FROM evaluation_jobs")):
        try:
            payload = (
                json.loads(row.request_payload)
                if isinstance(row.request_payload, str)
                else row.request_payload
            )
        except (TypeError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict) or payload.get("dataset_id") != row.dataset_id:
            payload_mismatches.append(row.id)
    if payload_mismatches:
        problems.append(
            "jobs whose payload dataset differs from the recorded dataset: "
            f"IDs {payload_mismatches[:20]}"
        )
    return problems


def _add_workspace_column(table: str, populate_sql: str) -> None:
    op.add_column(table, sa.Column("workspace_id", sa.Integer(), nullable=True))
    op.execute(sa.text(populate_sql))
    with op.batch_alter_table(table) as batch:
        batch.alter_column(
            "workspace_id",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=str(LEGACY_WORKSPACE_ID),
        )
        batch.create_foreign_key(
            f"fk_{table}_workspace_id_workspaces",
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(f"ix_{table}_workspace_id", ["workspace_id"])


def upgrade() -> None:
    problems = _ownership_problems()
    if problems:
        details = "; ".join(problems)
        raise RuntimeError(
            "workspace ownership migration stopped before changes because legacy "
            f"relationships are inconsistent: {details}. Repair or explicitly resolve "
            "these rows, then retry; no records were deleted."
        )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("is_legacy", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_workspaces_slug"),
    )
    op.create_table(
        "workspace_memberships",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'editor', 'viewer')",
            name="ck_workspace_membership_role",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "workspace_id"),
    )
    op.create_index(
        "ix_workspace_memberships_workspace_role",
        "workspace_memberships",
        ["workspace_id", "role"],
    )
    op.bulk_insert(
        sa.table(
            "workspaces",
            sa.column("id", sa.Integer()),
            sa.column("name", sa.String()),
            sa.column("slug", sa.String()),
            sa.column("is_legacy", sa.Boolean()),
            sa.column("created_at", sa.DateTime(timezone=True)),
        ),
        [
            {
                "id": LEGACY_WORKSPACE_ID,
                "name": "Legacy workspace",
                "slug": "legacy",
                "is_legacy": True,
                "created_at": datetime.now(UTC),
            }
        ],
    )

    _add_workspace_column("datasets", "UPDATE datasets SET workspace_id = 1")
    with op.batch_alter_table("datasets") as batch:
        batch.create_unique_constraint("uq_datasets_id_workspace", ["id", "workspace_id"])

    _add_workspace_column(
        "evaluation_runs",
        "UPDATE evaluation_runs SET workspace_id = "
        "(SELECT d.workspace_id FROM datasets d WHERE d.id = evaluation_runs.dataset_id)",
    )
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.create_foreign_key(
            "fk_evaluation_runs_dataset_workspace",
            "datasets",
            ["dataset_id", "workspace_id"],
            ["id", "workspace_id"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint("uq_evaluation_runs_id_workspace", ["id", "workspace_id"])

    op.add_column("evaluation_jobs", sa.Column("workspace_id", sa.Integer(), nullable=True))
    op.add_column("evaluation_jobs", sa.Column("submitted_by_user_id", sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE evaluation_jobs SET workspace_id = "
            "(SELECT d.workspace_id FROM datasets d WHERE d.id = evaluation_jobs.dataset_id)"
        )
    )
    with op.batch_alter_table("evaluation_jobs") as batch:
        batch.alter_column(
            "workspace_id",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=str(LEGACY_WORKSPACE_ID),
        )
        batch.create_foreign_key(
            "fk_evaluation_jobs_workspace_id_workspaces",
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_evaluation_jobs_submitted_by_user_id_users",
            "users",
            ["submitted_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_evaluation_jobs_dataset_workspace",
            "datasets",
            ["dataset_id", "workspace_id"],
            ["id", "workspace_id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_evaluation_jobs_workspace_id", ["workspace_id"])
        batch.create_index("ix_evaluation_jobs_submitted_by_user_id", ["submitted_by_user_id"])

    _add_workspace_column(
        "chat_sessions",
        "UPDATE chat_sessions SET workspace_id = "
        "(SELECT d.workspace_id FROM datasets d WHERE d.id = chat_sessions.dataset_id)",
    )
    op.add_column("chat_sessions", sa.Column("created_by_user_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("chat_sessions") as batch:
        batch.create_foreign_key(
            "fk_chat_sessions_created_by_user_id_users",
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_chat_sessions_dataset_workspace",
            "datasets",
            ["dataset_id", "workspace_id"],
            ["id", "workspace_id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_chat_sessions_created_by_user_id", ["created_by_user_id"])


def downgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_constraint("fk_chat_sessions_dataset_workspace", type_="foreignkey")
        batch.drop_constraint("fk_chat_sessions_created_by_user_id_users", type_="foreignkey")
        batch.drop_index("ix_chat_sessions_created_by_user_id")
        batch.drop_index("ix_chat_sessions_workspace_id")
        batch.drop_constraint("fk_chat_sessions_workspace_id_workspaces", type_="foreignkey")
        batch.drop_column("created_by_user_id")
        batch.drop_column("workspace_id")
    with op.batch_alter_table("evaluation_jobs") as batch:
        batch.drop_constraint("fk_evaluation_jobs_dataset_workspace", type_="foreignkey")
        batch.drop_constraint("fk_evaluation_jobs_submitted_by_user_id_users", type_="foreignkey")
        batch.drop_constraint("fk_evaluation_jobs_workspace_id_workspaces", type_="foreignkey")
        batch.drop_index("ix_evaluation_jobs_submitted_by_user_id")
        batch.drop_index("ix_evaluation_jobs_workspace_id")
        batch.drop_column("submitted_by_user_id")
        batch.drop_column("workspace_id")
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.drop_constraint("fk_evaluation_runs_dataset_workspace", type_="foreignkey")
        batch.drop_constraint("uq_evaluation_runs_id_workspace", type_="unique")
        batch.drop_index("ix_evaluation_runs_workspace_id")
        batch.drop_constraint("fk_evaluation_runs_workspace_id_workspaces", type_="foreignkey")
        batch.drop_column("workspace_id")
    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("uq_datasets_id_workspace", type_="unique")
        batch.drop_index("ix_datasets_workspace_id")
        batch.drop_constraint("fk_datasets_workspace_id_workspaces", type_="foreignkey")
        batch.drop_column("workspace_id")
    op.drop_index("ix_workspace_memberships_workspace_role", table_name="workspace_memberships")
    op.drop_table("workspace_memberships")
    op.drop_table("workspaces")
    op.drop_table("users")
