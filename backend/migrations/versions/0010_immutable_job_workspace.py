"""prevent changing a durable job's workspace after submission

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_evaluation_jobs_workspace_immutable
            BEFORE UPDATE OF workspace_id ON evaluation_jobs
            FOR EACH ROW WHEN NEW.workspace_id != OLD.workspace_id
            BEGIN
                SELECT RAISE(ABORT, 'evaluation job workspace is immutable');
            END
            """
        )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION evalops_reject_job_workspace_change()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
                    RAISE EXCEPTION 'evaluation job workspace is immutable';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_evaluation_jobs_workspace_immutable
            BEFORE UPDATE OF workspace_id ON evaluation_jobs
            FOR EACH ROW EXECUTE FUNCTION evalops_reject_job_workspace_change()
            """
        )
    else:
        raise RuntimeError(f"unsupported database dialect for job immutability: {dialect}")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_evaluation_jobs_workspace_immutable")
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_evaluation_jobs_workspace_immutable ON evaluation_jobs"
        )
        op.execute("DROP FUNCTION IF EXISTS evalops_reject_job_workspace_change()")
