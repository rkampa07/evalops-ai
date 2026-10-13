"""Pydantic request/response models (API contract)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .engine.metrics import check_json_schema_definition

Key = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_.\-]{1,100}$")]
Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_.:\-]{1,50}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

MAX_TEXT = 20_000
MAX_OUTPUT = 100_000
MAX_LATENCY_MS = 600_000


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrmOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- datasets


class DatasetCreate(StrictInput):
    name: Name
    description: str = Field(default="", max_length=2000)


class DatasetUpdate(StrictInput):
    name: Name | None = None
    description: str | None = Field(default=None, max_length=2000)


class DatasetOut(OrmOutput):
    id: int
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    test_case_count: int = 0
    run_count: int = 0


# ---------------------------------------------------------------- test cases


def _validate_schema(v: dict[str, Any] | None) -> dict[str, Any] | None:
    if v is not None:
        check_json_schema_definition(v)
    return v


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


class TestCaseBase(StrictInput):
    __test__ = False

    input: str = Field(min_length=1, max_length=MAX_TEXT)
    expected_output: str | None = Field(default=None, max_length=MAX_TEXT)
    required_terms: list[Term] = Field(default_factory=list, max_length=50)
    forbidden_terms: list[Term] = Field(default_factory=list, max_length=50)
    json_schema: dict[str, Any] | None = None
    max_latency_ms: float | None = Field(default=None, gt=0, le=MAX_LATENCY_MS)
    tags: list[Tag] = Field(default_factory=list, max_length=20)

    check_schema = field_validator("json_schema")(_validate_schema)
    dedupe_lists = field_validator("required_terms", "forbidden_terms", "tags")(_dedupe)


def has_any_check(data: dict[str, Any]) -> bool:
    return bool(
        data.get("expected_output") is not None
        or data.get("required_terms")
        or data.get("forbidden_terms")
        or data.get("json_schema") is not None
        or data.get("max_latency_ms") is not None
    )


class TestCaseCreate(TestCaseBase):
    __test__ = False

    key: Key

    @model_validator(mode="after")
    def _at_least_one_check(self) -> TestCaseCreate:
        if not has_any_check(self.model_dump()):
            raise ValueError(
                "a test case needs at least one check: expected_output, required_terms, "
                "forbidden_terms, json_schema, or max_latency_ms"
            )
        overlap = set(map(str.casefold, self.required_terms)) & set(
            map(str.casefold, self.forbidden_terms)
        )
        if overlap:
            raise ValueError(f"terms cannot be both required and forbidden: {sorted(overlap)}")
        return self


class TestCaseUpdate(StrictInput):
    __test__ = False

    key: Key | None = None
    input: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT)
    expected_output: str | None = Field(default=None, max_length=MAX_TEXT)
    required_terms: list[Term] | None = Field(default=None, max_length=50)
    forbidden_terms: list[Term] | None = Field(default=None, max_length=50)
    json_schema: dict[str, Any] | None = None
    max_latency_ms: float | None = Field(default=None, gt=0, le=MAX_LATENCY_MS)
    tags: list[Tag] | None = Field(default=None, max_length=20)

    check_schema = field_validator("json_schema")(_validate_schema)


class TestCaseBulkCreate(StrictInput):
    test_cases: list[TestCaseCreate] = Field(min_length=1, max_length=2000)


class TestCaseOut(OrmOutput):
    __test__ = False

    id: int
    dataset_id: int
    key: str
    input: str
    expected_output: str | None
    required_terms: list[str]
    forbidden_terms: list[str]
    json_schema: dict[str, Any] | None
    max_latency_ms: float | None
    tags: list[str]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- runs


class EvalConfigIn(StrictInput):
    min_token_f1: float = Field(default=0.5, ge=0, le=1)
    require_exact_match: bool = False
    case_sensitive: bool = False
    default_max_latency_ms: float | None = Field(default=None, gt=0, le=MAX_LATENCY_MS)


class ResponseIn(StrictInput):
    case_key: Key
    output: str = Field(max_length=MAX_OUTPUT)
    latency_ms: float | None = Field(default=None, ge=0, le=MAX_LATENCY_MS * 10)


class RunCreate(StrictInput):
    dataset_id: int = Field(gt=0)
    model_name: Name
    prompt_version: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    label: str = Field(default="", max_length=200)
    config: EvalConfigIn = Field(default_factory=EvalConfigIn)
    responses: list[ResponseIn] = Field(min_length=1, max_length=5000)

    @field_validator("responses")
    @classmethod
    def _unique_keys(cls, v: list[ResponseIn]) -> list[ResponseIn]:
        seen: set[str] = set()
        dupes: set[str] = set()
        for r in v:
            (dupes if r.case_key in seen else seen).add(r.case_key)
        if dupes:
            raise ValueError(f"duplicate case_key in responses: {sorted(dupes)}")
        return v


class Failure(BaseModel):
    check: str
    message: str


class ResultOut(OrmOutput):
    id: int
    case_key: str
    test_case_id: int | None
    output: str | None
    latency_ms: float | None
    passed: bool
    metrics: dict[str, Any]
    failures: list[Failure]
    tags: list[str]


class TagPassRate(BaseModel):
    cases: int
    pass_rate: float | None


class RunSummary(BaseModel):
    total_cases: int
    responded_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float | None
    exact_match_rate: float | None
    mean_token_precision: float | None
    mean_token_recall: float | None
    mean_token_f1: float | None
    required_terms_pass_rate: float | None
    forbidden_terms_pass_rate: float | None
    forbidden_violation_count: int
    schema_pass_rate: float | None
    latency_pass_rate: float | None
    latency_mean_ms: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    latency_max_ms: float | None
    failure_counts: dict[str, int]
    tag_pass_rates: dict[str, TagPassRate]


class RunOut(OrmOutput):
    id: int
    dataset_id: int
    dataset_name: str | None = None
    model_name: str
    prompt_version: str
    label: str
    status: str
    config: EvalConfigIn
    summary: RunSummary
    pass_rate: float | None
    created_at: datetime


class RunDetailOut(RunOut):
    results: list[ResultOut]


class RunList(BaseModel):
    items: list[RunOut]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------- comparison & gates


class CompareConfigIn(StrictInput):
    metric_tolerance: float = Field(default=0.02, ge=0, le=1)
    latency_tolerance_pct: float = Field(default=0.2, ge=0, le=10)
    case_f1_drop_threshold: float = Field(default=0.15, ge=0, le=1)


class MetricDelta(BaseModel):
    metric: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    direction: Literal["higher_is_better", "lower_is_better"]
    status: Literal["improved", "regressed", "unchanged", "not_applicable"]


class CaseChange(BaseModel):
    case_key: str
    change: Literal[
        "regressed",
        "fixed",
        "f1_drop",
        "unchanged_pass",
        "unchanged_fail",
        "only_in_baseline",
        "only_in_candidate",
    ]
    baseline_passed: bool | None
    candidate_passed: bool | None
    baseline_token_f1: float | None
    candidate_token_f1: float | None
    token_f1_delta: float | None
    candidate_failures: list[Failure]


class RunRef(BaseModel):
    id: int
    model_name: str
    prompt_version: str
    label: str
    created_at: datetime


class ComparisonOut(BaseModel):
    dataset_id: int
    baseline: RunRef
    candidate: RunRef
    has_regression: bool
    regression_reasons: list[str]
    metric_deltas: list[MetricDelta]
    case_counts: dict[str, int]
    case_changes: list[CaseChange]
    config: CompareConfigIn


class GateConfigIn(StrictInput):
    min_pass_rate: float | None = Field(default=None, ge=0, le=1)
    min_mean_token_f1: float | None = Field(default=None, ge=0, le=1)
    min_exact_match_rate: float | None = Field(default=None, ge=0, le=1)
    min_required_terms_pass_rate: float | None = Field(default=None, ge=0, le=1)
    min_schema_pass_rate: float | None = Field(default=None, ge=0, le=1)
    min_latency_pass_rate: float | None = Field(default=None, ge=0, le=1)
    max_forbidden_violations: int | None = Field(default=None, ge=0)
    max_p95_latency_ms: float | None = Field(default=None, gt=0)
    max_failed_cases: int | None = Field(default=None, ge=0)
    max_pass_rate_drop: float | None = Field(default=None, ge=0, le=1)
    max_regressed_cases: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _non_empty(self) -> GateConfigIn:
        if not self.model_dump(exclude_none=True):
            raise ValueError("configure at least one gate")
        return self


class GateRequest(StrictInput):
    candidate_run_id: int = Field(gt=0)
    baseline_run_id: int | None = Field(default=None, gt=0)
    gates: GateConfigIn
    compare: CompareConfigIn = Field(default_factory=CompareConfigIn)

    @model_validator(mode="after")
    def _distinct(self) -> GateRequest:
        if self.baseline_run_id == self.candidate_run_id:
            raise ValueError("baseline_run_id and candidate_run_id must differ")
        return self


class GateResult(BaseModel):
    gate: str
    metric: str
    comparator: Literal[">=", "<="]
    threshold: float
    actual: float | None
    status: Literal["pass", "fail", "skipped"]
    message: str


class GateEvaluationOut(BaseModel):
    candidate_run_id: int
    baseline_run_id: int | None
    decision: Literal["pass", "fail"]
    passed_gates: int
    failed_gates: int
    skipped_gates: int
    gates: list[GateResult]
    regression_detected: bool | None


class ReportOut(BaseModel):
    title: str
    generated_at: datetime
    run: RunOut
    dataset_name: str
    baseline: RunRef | None
    readiness: Literal["ready", "not_ready"]
    gate_policy: dict[str, float]
    gate_evaluation: GateEvaluationOut
    comparison: ComparisonOut | None
    failing_cases: list[ResultOut]
    notes: list[str]
    markdown: str
