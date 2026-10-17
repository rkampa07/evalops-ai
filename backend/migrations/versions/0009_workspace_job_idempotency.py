"""scope evaluation job idempotency keys to workspace and submitter

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evaluation_jobs",
        sa.Column("submitter_key", sa.String(length=100), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE evaluation_jobs SET submitter_key = CASE "
            "WHEN submitted_by_user_id IS NULL THEN 'dev:local' "
            "ELSE 'user:' || submitted_by_user_id END"
        )
    )
    with op.batch_alter_table("evaluation_jobs") as batch:
        batch.alter_column(
            "submitter_key",
            existing_type=sa.String(length=100),
            nullable=False,
            server_default="dev:local",
        )
        batch.drop_constraint("uq_evaluation_job_idempotency_key", type_="unique")
        batch.create_unique_constraint(
            "uq_evaluation_job_workspace_submitter_key",
            ["workspace_id", "submitter_key", "idempotency_key"],
        )


def downgrade() -> None:
    # The old global constraint cannot be restored if different workspaces reused a key.
    # Refuse a lossy downgrade rather than deleting or rewriting job history.
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT idempotency_key FROM evaluation_jobs GROUP BY idempotency_key "
            "HAVING count(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicates:
        raise RuntimeError("cannot downgrade: idempotency keys are reused across workspaces")
    with op.batch_alter_table("evaluation_jobs") as batch:
        batch.drop_constraint("uq_evaluation_job_workspace_submitter_key", type_="unique")
        batch.create_unique_constraint("uq_evaluation_job_idempotency_key", ["idempotency_key"])
        batch.drop_column("submitter_key")
