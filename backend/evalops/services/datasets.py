"""Business rules for datasets and test cases."""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..engine.versioning import case_definition, definition_hash
from ..errors import ConflictError, NotFoundError, UnprocessableError
from ..models import Dataset, Passage, TestCase
from ..repositories.datasets import DatasetRepository, TestCaseRepository
from ..schemas import (
    DatasetCreate,
    DatasetOut,
    DatasetUpdate,
    PassageIn,
    PassageUpdate,
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
    "relevant_passage_ids",
    "expect_abstain",
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
        out.passage_count = len(ds.passages)
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
        dataset = self.get_dataset(dataset_id)
        keys = [c.key for c in items]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise UnprocessableError(f"duplicate keys in request: {dupes}")
        clash = sorted(self.cases.existing_keys(dataset_id) & set(keys))
        if clash:
            raise ConflictError(f"test case key(s) already exist in dataset: {clash}")
        rows = []
        for item in items:
            data = item.model_dump()
            rows.append(
                TestCase(
                    dataset_id=dataset_id,
                    version=1,
                    content_hash=definition_hash(case_definition(data)),
                    **data,
                )
            )
        self.cases.add_all(rows)
        dataset.version += 1
        self.s.commit()
        return rows

    def update_case(self, case_id: int, data: TestCaseUpdate) -> TestCase:
        case = self.get_case(case_id)
        changes = data.model_dump(exclude_unset=True)
        for required in ("key", "input"):
            if required in changes and changes[required] is None:
                raise UnprocessableError(f"{required} cannot be null")
        if "expect_abstain" in changes and changes["expect_abstain"] is None:
            changes["expect_abstain"] = False
        for list_field in ("required_terms", "forbidden_terms", "tags", "relevant_passage_ids"):
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

        new_values = validated.model_dump()
        new_hash = definition_hash(case_definition(new_values))
        definition_changed = new_hash != case.content_hash
        key_changed = validated.key != case.key
        for field, value in new_values.items():
            setattr(case, field, value)
        if definition_changed:
            case.version += 1
            case.content_hash = new_hash
        if definition_changed or key_changed:
            # Tags are labels, not scoring rules: editing only tags keeps versions unchanged.
            case.dataset.version += 1
        self.s.commit()
        return case

    def delete_case(self, case_id: int) -> None:
        case = self.get_case(case_id)
        case.dataset.version += 1
        self.cases.delete(case)
        self.s.commit()

    # ------------------------------------------------------------ knowledge base passages

    def list_passages(self, dataset_id: int) -> list[Passage]:
        return list(self.get_dataset(dataset_id).passages)

    def _get_passage(self, dataset: Dataset, passage_id: str) -> Passage:
        for passage in dataset.passages:
            if passage.passage_id == passage_id:
                return passage
        raise NotFoundError(f"passage {passage_id!r} not found in dataset {dataset.id}")

    def create_passages(self, dataset_id: int, items: list[PassageIn]) -> list[Passage]:
        """Create passages atomically; duplicate ids reject the whole batch."""
        dataset = self.get_dataset(dataset_id)
        ids = [p.passage_id for p in items]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise UnprocessableError(f"duplicate passage ids in request: {dupes}")
        clash = sorted({p.passage_id for p in dataset.passages} & set(ids))
        if clash:
            raise ConflictError(f"passage id(s) already exist in dataset: {clash}")
        rows = [Passage(dataset_id=dataset_id, **p.model_dump()) for p in items]
        self.s.add_all(rows)
        dataset.version += 1  # evidence changed: RAG scores may change
        self.s.commit()
        return rows

    def update_passage(self, dataset_id: int, passage_id: str, data: PassageUpdate) -> Passage:
        dataset = self.get_dataset(dataset_id)
        passage = self._get_passage(dataset, passage_id)
        changes = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
        if "text" in changes and changes["text"] != passage.text:
            dataset.version += 1
        for field, value in changes.items():
            setattr(passage, field, value)
        self.s.commit()
        return passage

    def delete_passage(self, dataset_id: int, passage_id: str) -> None:
        dataset = self.get_dataset(dataset_id)
        passage = self._get_passage(dataset, passage_id)
        self.s.delete(passage)
        dataset.version += 1
        self.s.commit()
