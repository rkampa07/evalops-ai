"""Persistence for datasets and test cases. No business rules here."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Dataset, EvaluationRun, Passage, TestCase


class DatasetRepository:
    def __init__(self, session: Session):
        self.s = session

    def list(
        self, *, workspace_id: int, limit: int | None = None, offset: int = 0
    ) -> list[Dataset]:
        stmt = (
            select(Dataset)
            .where(Dataset.workspace_id == workspace_id)
            .order_by(Dataset.name, Dataset.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        return list(self.s.scalars(stmt))

    def get(self, dataset_id: int, *, workspace_id: int) -> Dataset | None:
        return self.s.scalar(
            select(Dataset).where(Dataset.id == dataset_id, Dataset.workspace_id == workspace_id)
        )

    def get_by_name(self, name: str, *, workspace_id: int) -> Dataset | None:
        return self.s.scalar(
            select(Dataset).where(Dataset.name == name, Dataset.workspace_id == workspace_id)
        )

    def add(self, dataset: Dataset) -> Dataset:
        self.s.add(dataset)
        self.s.flush()
        return dataset

    def delete(self, dataset: Dataset) -> None:
        self.s.delete(dataset)

    def counts(self, dataset_ids: list[int] | None = None) -> tuple[dict[int, int], dict[int, int]]:
        if dataset_ids == []:
            return {}, {}
        cases_stmt = select(TestCase.dataset_id, func.count()).group_by(TestCase.dataset_id)
        runs_stmt = select(EvaluationRun.dataset_id, func.count()).group_by(
            EvaluationRun.dataset_id
        )
        if dataset_ids is not None:
            cases_stmt = cases_stmt.where(TestCase.dataset_id.in_(dataset_ids))
            runs_stmt = runs_stmt.where(EvaluationRun.dataset_id.in_(dataset_ids))
        cases = dict(self.s.execute(cases_stmt).all())
        runs = dict(self.s.execute(runs_stmt).all())
        return cases, runs

    def passage_counts(self, dataset_ids: list[int] | None = None) -> dict[int, int]:
        if dataset_ids == []:
            return {}
        stmt = select(Passage.dataset_id, func.count()).group_by(Passage.dataset_id)
        if dataset_ids is not None:
            stmt = stmt.where(Passage.dataset_id.in_(dataset_ids))
        return dict(self.s.execute(stmt).all())

    def count_datasets(self, *, workspace_id: int) -> int:
        return (
            self.s.scalar(
                select(func.count())
                .select_from(Dataset)
                .where(Dataset.workspace_id == workspace_id)
            )
            or 0
        )

    def passage_count(self, dataset_id: int) -> int:
        return (
            self.s.scalar(
                select(func.count()).select_from(Passage).where(Passage.dataset_id == dataset_id)
            )
            or 0
        )

    def list_passages(
        self, dataset_id: int, *, limit: int | None = None, offset: int = 0
    ) -> list[Passage]:
        stmt = (
            select(Passage)
            .where(Passage.dataset_id == dataset_id)
            .order_by(Passage.passage_id, Passage.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        return list(self.s.scalars(stmt))


class TestCaseRepository:
    __test__ = False

    def __init__(self, session: Session):
        self.s = session

    def list_for_dataset(
        self, dataset_id: int, *, limit: int | None = None, offset: int = 0
    ) -> list[TestCase]:
        stmt = (
            select(TestCase)
            .where(TestCase.dataset_id == dataset_id)
            .order_by(TestCase.key, TestCase.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        return list(self.s.scalars(stmt))

    def count_for_dataset(self, dataset_id: int) -> int:
        return (
            self.s.scalar(
                select(func.count()).select_from(TestCase).where(TestCase.dataset_id == dataset_id)
            )
            or 0
        )

    def get(self, case_id: int, *, workspace_id: int) -> TestCase | None:
        return self.s.scalar(
            select(TestCase)
            .join(Dataset)
            .where(TestCase.id == case_id, Dataset.workspace_id == workspace_id)
        )

    def get_by_key(self, dataset_id: int, key: str) -> TestCase | None:
        stmt = select(TestCase).where(TestCase.dataset_id == dataset_id, TestCase.key == key)
        return self.s.scalar(stmt)

    def existing_keys(self, dataset_id: int) -> set[str]:
        return set(self.s.scalars(select(TestCase.key).where(TestCase.dataset_id == dataset_id)))

    def add_all(self, cases: list[TestCase]) -> list[TestCase]:
        self.s.add_all(cases)
        self.s.flush()
        return cases

    def delete(self, case: TestCase) -> None:
        self.s.delete(case)
