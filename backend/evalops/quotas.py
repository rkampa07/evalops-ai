"""Database-serialized resource quota checks for workspace writes."""

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .errors import NotFoundError
from .models import Dataset, EvaluationJob, Passage, TestCase, Workspace


def begin_workspace_write(session: Session, workspace_id: int) -> None:
    """Serialize quota check plus write against the workspace row (or SQLite writer lock)."""
    session.commit()
    session.expire_all()
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))
    else:
        workspace = session.scalar(
            select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()
        )
        if workspace is None:
            raise NotFoundError(f"workspace {workspace_id} not found")


def count_datasets(session: Session, workspace_id: int) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(Dataset).where(Dataset.workspace_id == workspace_id)
        )
        or 0
    )


def count_test_cases(session: Session, dataset_id: int) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(TestCase).where(TestCase.dataset_id == dataset_id)
        )
        or 0
    )


def count_passages(session: Session, workspace_id: int) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Passage)
            .join(Dataset, Dataset.id == Passage.dataset_id)
            .where(Dataset.workspace_id == workspace_id)
        )
        or 0
    )


def count_workspace_queued_jobs(
    session: Session, workspace_id: int, *, exclude_job_id: int | None = None
) -> int:
    stmt = (
        select(func.count())
        .select_from(EvaluationJob)
        .where(
            EvaluationJob.workspace_id == workspace_id,
            (EvaluationJob.status == "queued")
            | ((EvaluationJob.status == "running") & EvaluationJob.lease_owner.is_(None)),
        )
    )
    if exclude_job_id is not None:
        stmt = stmt.where(EvaluationJob.id != exclude_job_id)
    return session.scalar(stmt) or 0


def count_workspace_active_jobs(session: Session, workspace_id: int) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(EvaluationJob)
            .where(
                EvaluationJob.workspace_id == workspace_id,
                EvaluationJob.status == "running",
                EvaluationJob.lease_owner.is_not(None),
            )
        )
        or 0
    )
