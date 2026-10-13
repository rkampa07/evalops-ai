"""Business rules for datasets and test cases."""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..errors import ConflictError, NotFoundError, UnprocessableError
from ..models import Dataset, TestCase
from ..repositories.datasets import DatasetRepository, TestCaseRepository
from ..schemas import (
    DatasetCreate,
    DatasetOut,
    DatasetUpdate,
    TestCaseCreate,
    TestCaseUpdate,
)

_CASE_FIELDS = (
    "key",
    "input",
    "expected_output",
    "required_terms",
    "forbidden_terms",
    "json_schema",
    "max_latency_ms",
    "tags",
)


class DatasetService:
    def __init__(self, session: Session):
        self.s = session
        self.datasets = DatasetRepository(session)
        self.cases = TestCaseRepository(session)

    # ------------------------------------------------------------ datasets

    def _out(
        self, ds: Dataset, case_counts: dict[int, int], run_counts: dict[int, int]
    ) -> DatasetOut:
        out = DatasetOut.model_validate(ds)
        out.test_case_count = case_counts.get(ds.id, 0)
        out.run_count = run_counts.get(ds.id, 0)
        return out

    def list_datasets(self) -> list[DatasetOut]:
        cases, runs = self.datasets.counts()
        return [self._out(ds, cases, runs) for ds in self.datasets.list()]

    def get_dataset(self, dataset_id: int) -> Dataset:
        ds = self.datasets.get(dataset_id)
        if ds is None:
            raise NotFoundError(f"dataset {dataset_id} not found")
        return ds

    def get_dataset_out(self, dataset_id: int) -> DatasetOut:
        ds = self.get_dataset(dataset_id)
        cases, runs = self.datasets.counts()
        return self._out(ds, cases, runs)

    def create_dataset(self, data: DatasetCreate) -> DatasetOut:
        if self.datasets.get_by_name(data.name):
            raise ConflictError(f"dataset named {data.name!r} already exists")
        ds = self.datasets.add(Dataset(name=data.name, description=data.description))
        self.s.commit()
        return self.get_dataset_out(ds.id)

    def update_dataset(self, dataset_id: int, data: DatasetUpdate) -> DatasetOut:
        ds = self.get_dataset(dataset_id)
        changes = data.model_dump(exclude_unset=True)
        if changes.get("name") is None:
            changes.pop("name", None)
        if changes.get("description") is None:
            changes.pop("description", None)
        new_name = changes.get("name")
        if new_name and new_name != ds.name and self.datasets.get_by_name(new_name):
            raise ConflictError(f"dataset named {new_name!r} already exists")
        for field, value in changes.items():
            setattr(ds, field, value)
        self.s.commit()
        return self.get_dataset_out(ds.id)

    def delete_dataset(self, dataset_id: int) -> None:
        ds = self.get_dataset(dataset_id)
        if ds.runs:
            raise ConflictError(
                f"dataset {dataset_id} has {len(ds.runs)} evaluation run(s); delete them first"
            )
        self.datasets.delete(ds)
        self.s.commit()

    # ------------------------------------------------------------ test cases

    def list_cases(self, dataset_id: int) -> list[TestCase]:
        self.get_dataset(dataset_id)
        return self.cases.list_for_dataset(dataset_id)

    def get_case(self, case_id: int) -> TestCase:
        case = self.cases.get(case_id)
        if case is None:
            raise NotFoundError(f"test case {case_id} not found")
        return case

    def create_cases(self, dataset_id: int, items: list[TestCaseCreate]) -> list[TestCase]:
        """Create one or many cases atomically; any key conflict rejects the whole batch."""
        self.get_dataset(dataset_id)
        keys = [c.key for c in items]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise UnprocessableError(f"duplicate keys in request: {dupes}")
        clash = sorted(self.cases.existing_keys(dataset_id) & set(keys))
        if clash:
            raise ConflictError(f"test case key(s) already exist in dataset: {clash}")
        rows = [TestCase(dataset_id=dataset_id, **c.model_dump()) for c in items]
        self.cases.add_all(rows)
        self.s.commit()
        return rows

    def update_case(self, case_id: int, data: TestCaseUpdate) -> TestCase:
        case = self.get_case(case_id)
        changes = data.model_dump(exclude_unset=True)
        for required in ("key", "input"):
            if required in changes and changes[required] is None:
                raise UnprocessableError(f"{required} cannot be null")
        for list_field in ("required_terms", "forbidden_terms", "tags"):
            if list_field in changes and changes[list_field] is None:
                changes[list_field] = []

        merged = {f: getattr(case, f) for f in _CASE_FIELDS} | changes
        try:
            validated = TestCaseCreate.model_validate(merged)
        except ValidationError as exc:
            msg = exc.errors()[0]["msg"] if exc.errors() else "invalid test case"
            raise UnprocessableError(msg.removeprefix("Value error, ")) from exc

        if validated.key != case.key and self.cases.get_by_key(case.dataset_id, validated.key):
            raise ConflictError(f"test case key {validated.key!r} already exists in dataset")
        for field, value in validated.model_dump().items():
            setattr(case, field, value)
        self.s.commit()
        return case

    def delete_case(self, case_id: int) -> None:
        self.cases.delete(self.get_case(case_id))
        self.s.commit()
