"""Persistence for evaluation runs and their per-case results."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..models import EvaluationRun


class RunRepository:
    def __init__(self, session: Session):
        self.s = session

    def add(self, run: EvaluationRun) -> EvaluationRun:
        self.s.add(run)
        self.s.flush()
        return run

    def get(self, run_id: int, *, with_results: bool = False) -> EvaluationRun | None:
        stmt = select(EvaluationRun).where(EvaluationRun.id == run_id)
        stmt = stmt.options(selectinload(EvaluationRun.dataset))
        if with_results:
            stmt = stmt.options(selectinload(EvaluationRun.results))
        return self.s.scalar(stmt)

    def list(
        self,
        *,
        dataset_id: int | None = None,
        model_name: str | None = None,
        prompt_version: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[EvaluationRun], int]:
        stmt = select(EvaluationRun)
        if dataset_id is not None:
            stmt = stmt.where(EvaluationRun.dataset_id == dataset_id)
        if model_name:
            stmt = stmt.where(EvaluationRun.model_name == model_name)
        if prompt_version:
            stmt = stmt.where(EvaluationRun.prompt_version == prompt_version)
        total = self.s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        stmt = (
            stmt.options(selectinload(EvaluationRun.dataset))
            .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.s.scalars(stmt)), total

    def delete(self, run: EvaluationRun) -> None:
        self.s.delete(run)
