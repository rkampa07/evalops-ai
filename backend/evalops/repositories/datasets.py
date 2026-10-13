"""Persistence for datasets and test cases. No business rules here."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Dataset, EvaluationRun, TestCase


class DatasetRepository:
    def __init__(self, session: Session):
        self.s = session

    def list(self) -> list[Dataset]:
        return list(self.s.scalars(select(Dataset).order_by(Dataset.name)))

    def get(self, dataset_id: int) -> Dataset | None:
        return self.s.get(Dataset, dataset_id)

    def get_by_name(self, name: str) -> Dataset | None:
        return self.s.scalar(select(Dataset).where(Dataset.name == name))

    def add(self, dataset: Dataset) -> Dataset:
        self.s.add(dataset)
        self.s.flush()
        return dataset

    def delete(self, dataset: Dataset) -> None:
        self.s.delete(dataset)

    def counts(self) -> tuple[dict[int, int], dict[int, int]]:
        cases = dict(
            self.s.execute(
                select(TestCase.dataset_id, func.count()).group_by(TestCase.dataset_id)
            ).all()
        )
        runs = dict(
            self.s.execute(
                select(EvaluationRun.dataset_id, func.count()).group_by(EvaluationRun.dataset_id)
            ).all()
        )
        return cases, runs


class TestCaseRepository:
    __test__ = False

    def __init__(self, session: Session):
        self.s = session

    def list_for_dataset(self, dataset_id: int) -> list[TestCase]:
        stmt = select(TestCase).where(TestCase.dataset_id == dataset_id).order_by(TestCase.key)
        return list(self.s.scalars(stmt))

    def get(self, case_id: int) -> TestCase | None:
        return self.s.get(TestCase, case_id)

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
