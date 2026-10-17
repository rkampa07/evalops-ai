"""Persistence helpers for durable evaluation job submissions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EvaluationJob, EvaluationJobQueue


class EvaluationJobRepository:
    def __init__(self, session: Session):
        self.s = session

    def get(
        self, job_id: int, *, workspace_id: int, user_id: int | None, for_update: bool = False
    ) -> EvaluationJob | None:
        stmt = select(EvaluationJob).where(
            EvaluationJob.id == job_id,
            EvaluationJob.workspace_id == workspace_id,
            EvaluationJob.submitted_by_user_id == user_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self.s.scalar(stmt)

    def get_by_idempotency_key(
        self, key: str, *, workspace_id: int, submitter_key: str, for_update: bool = False
    ) -> EvaluationJob | None:
        stmt = select(EvaluationJob).where(
            EvaluationJob.idempotency_key == key,
            EvaluationJob.workspace_id == workspace_id,
            EvaluationJob.submitter_key == submitter_key,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self.s.scalar(stmt)

    def queue_state(self, *, for_update: bool = False) -> EvaluationJobQueue | None:
        stmt = select(EvaluationJobQueue).where(EvaluationJobQueue.id == 1)
        if for_update:
            stmt = stmt.with_for_update()
        return self.s.scalar(stmt)
