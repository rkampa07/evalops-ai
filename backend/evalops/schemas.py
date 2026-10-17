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
# Passage ids double as citation markers in answers, e.g. "[KB-RET-01]" (see engine/rag.py).
PassageId = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+){1,5}$")
]
MAX_METADATA_BYTES = 16_384

MAX_TEXT = 20_000
MAX_OUTPUT = 100_000
MAX_LATENCY_MS = 600_000


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrmOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[PageItem](BaseModel):
    """Opt-in page envelope; legacy list endpoints remain arrays without query parameters."""

    items: list[PageItem]
    total: int
    limit: int
    offset: int


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
    version: int
    created_at: datetime
    updated_at: datetime
    test_case_count: int = 0
    run_count: int = 0
    passage_count: int = 0


# ---------------------------------------------------------------- knowledge base passages


class PassageIn(StrictInput):
    passage_id: PassageId
    title: str = Field(default="", max_length=300)
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class PassageBulkCreate(StrictInput):
    passages: list[PassageIn] = Field(min_length=1, max_length=1000)


class PassageUpdate(StrictInput):
    title: str | None = Field(default=None, max_length=300)
    text: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT)


class PassageOut(OrmOutput):
    passage_id: str
    title: str
    text: str
    created_at: datetime
    updated_at: datetime


def _bounded_metadata(value: dict[str, Any] | None) -> dict[str, Any] | None:
    import json

    if value is not None and len(json.dumps(value, default=str)) > MAX_METADATA_BYTES:
        raise ValueError(f"metadata larger than {MAX_METADATA_BYTES} bytes")
    return value


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
    # RAG: gold passages that answer the question; or the question is unanswerable from the
    # knowledge base and the assistant should decline.
    relevant_passage_ids: list[PassageId] = Field(default_factory=list, max_length=20)
    expect_abstain: bool = False

    check_schema = field_validator("json_schema")(_validate_schema)
    dedupe_lists = field_validator(
        "required_terms", "forbidden_terms", "tags", "relevant_passage_ids"
    )(_dedupe)


def has_any_check(data: dict[str, Any]) -> bool:
    return bool(
        data.get("expected_output") is not None
        or data.get("required_terms")
        or data.get("forbidden_terms")
        or data.get("json_schema") is not None
        or data.get("max_latency_ms") is not None
        or data.get("relevant_passage_ids")
        or data.get("expect_abstain")
    )


class TestCaseCreate(TestCaseBase):
    __test__ = False

    key: Key

    @model_validator(mode="after")
    def _at_least_one_check(self) -> TestCaseCreate:
        if not has_any_check(self.model_dump()):
            raise ValueError(
                "a test case needs at least one check: expected_output, required_terms, "
                "forbidden_terms, json_schema, max_latency_ms, relevant_passage_ids, or "
                "expect_abstain"
            )
        overlap = set(map(str.casefold, self.required_terms)) & set(
            map(str.casefold, self.forbidden_terms)
        )
        if overlap:
            raise ValueError(f"terms cannot be both required and forbidden: {sorted(overlap)}")
        if self.expect_abstain and self.relevant_passage_ids:
            raise ValueError("a case that expects abstention cannot list relevant passages")
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
    relevant_passage_ids: list[PassageId] | None = Field(default=None, max_length=20)
    expect_abstain: bool | None = None

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
    version: int
    content_hash: str
    relevant_passage_ids: list[str] = Field(default_factory=list)
    expect_abstain: bool = False


# ---------------------------------------------------------------- runs


class EvalConfigIn(StrictInput):
    min_token_f1: float = Field(default=0.5, ge=0, le=1)
    require_exact_match: bool = False
    case_sensitive: bool = False
    default_max_latency_ms: float | None = Field(default=None, gt=0, le=MAX_LATENCY_MS)
    min_groundedness: float = Field(default=0.8, ge=0, le=1)


class ResponseIn(StrictInput):
    case_key: Key
    output: str = Field(max_length=MAX_OUTPUT)
    latency_ms: float | None = Field(default=None, ge=0, le=MAX_LATENCY_MS * 10)
    # RAG: passage ids placed in the model's context, in rank order.
    retrieved_passage_ids: list[PassageId] | None = Field(default=None, max_length=50)
    # Per-response generation metadata (tokens, timings, ...). Stored, not interpreted.
    generation: dict[str, Any] | None = None

    bounded = field_validator("generation")(_bounded_metadata)


class RunCreate(StrictInput):
    dataset_id: int = Field(gt=0)
    model_name: Name
    prompt_version: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    label: str = Field(default="", max_length=200)
    config: EvalConfigIn = Field(default_factory=EvalConfigIn)
    responses: list[ResponseIn] = Field(min_length=1, max_length=5000)
    # Run-level generation metadata: model id/revision, prompt template hash, settings, ...
    generation: dict[str, Any] | None = None

    bounded = field_validator("generation")(_bounded_metadata)

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
    retrieved_passage_ids: list[str] | None = None
    generation: dict[str, Any] | None = None
    model_grades: dict[str, Any] | None = None
    case_version: int | None = None
    case_hash: str | None = None
    case_snapshot: dict[str, Any] | None = None


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
    # RAG (deterministic); absent/None for runs without RAG cases or created before 1.2.0
    rag_cases: int = 0
    retrieval_recall_at_k: float | None = None
    mrr: float | None = None
    citation_validity_rate: float | None = None
    citation_support_rate: float | None = None
    groundedness: float | None = None
    unsupported_claim_count: int = 0
    abstention_accuracy: float | None = None


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
    methodology_version: str
    dataset_version: int | None
    knowledge_base_hash: str | None = None
    generation: dict[str, Any] | None = None
    # Model-based (estimated) aggregates; never mixed into the deterministic summary.
    model_graded: dict[str, Any] | None = None
    created_at: datetime


class RunDetailOut(RunOut):
    results: list[ResultOut]


class RunList(BaseModel):
    items: list[RunOut]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------- durable evaluation jobs

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class JobErrorOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class JobProgressOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed: int
    total: int
    fraction: float | None


class EvaluationJobOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: int
    status: JobStatus
    status_url: str
    dataset_id: int
    dataset_version: int
    scoring_input_fingerprint: str
    knowledge_base_hash: str | None
    progress: JobProgressOut
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    heartbeat_at: datetime | None
    finished_at: datetime | None
    attempt_count: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    cancellation_requested_at: datetime | None
    error: JobErrorOut | None
    run_id: int | None
    retry_of_job_id: int | None
    replayed: bool = False


# ---------------------------------------------------------------- frozen citation assessments

CitationAssessmentStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
CitationPredictionStatus = Literal[
    "correct",
    "wrong_passage",
    "unrelated",
    "not_retrieved",
    "partial",
    "not_applicable",
    "missing_citation",
    "invalid_citation",
    "unresolved",
]


class CitationAssessmentCreate(StrictInput):
    evaluator: Literal["citation"]
    citation_expected: bool


class ClaimAssessmentCreate(StrictInput):
    evaluator: Literal["claim"]


AssessmentCreate = Annotated[
    CitationAssessmentCreate | ClaimAssessmentCreate,
    Field(discriminator="evaluator"),
]


class CitationAssessmentJobOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_id: int
    evaluator: Literal["citation"]
    status: CitationAssessmentStatus
    status_url: str
    result_url: str
    run_id: int
    result_id: int
    progress: JobProgressOut
    evaluator_version: str
    predictor_sha256: str
    runner_sha256: str
    config_digest: str
    input_fingerprint: str
    knowledge_base_hash: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    heartbeat_at: datetime | None
    finished_at: datetime | None
    attempt_count: int
    cancellation_requested: bool
    error: JobErrorOut | None
    replayed: bool = False


class CitationDecisionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_id: str | None
    status: CitationPredictionStatus
    reason_code: str
    reason: str
    component_coverage: list[float]


class CitationAssessmentResultOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_id: int
    run_id: int
    result_id: int
    case_key: str
    evaluator_version: str
    predictor_sha256: str
    runner_sha256: str
    config_digest: str
    input_fingerprint: str
    knowledge_base_hash: str | None
    status: CitationPredictionStatus
    reason_code: str
    reason: str
    per_citation: list[CitationDecisionOut]
    created_at: datetime


class ClaimAssessmentJobOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_id: int
    evaluator: Literal["claim"]
    status: CitationAssessmentStatus
    status_url: str
    result_url: str
    run_id: int
    result_id: int
    progress: JobProgressOut
    evaluator_version: str
    source_sha256: str
    config_digest: str
    model_id: str
    model_revision: str
    input_fingerprint: str
    knowledge_base_hash: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    heartbeat_at: datetime | None
    finished_at: datetime | None
    attempt_count: int
    cancellation_requested: bool
    error: JobErrorOut | None
    replayed: bool = False


ClaimLabel = Literal["supported", "contradicted", "insufficient_evidence", "needs_human_review"]


class ClaimVerdictOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_text: str
    label: ClaimLabel
    abstained: bool
    reason: str
    diagnostic_branch: str
    deciding_evidence: list[dict[str, Any]]
    guards: list[str]
    config: dict[str, Any]
    model: dict[str, Any]
    uncalibrated_scores: list[dict[str, Any]]
    ignored_contradictions: list[dict[str, Any]]


class ClaimAssessmentResultOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_id: int
    run_id: int
    result_id: int
    case_key: str
    evaluator_version: str
    source_sha256: str
    config_digest: str
    model_id: str
    model_revision: str
    input_fingerprint: str
    knowledge_base_hash: str | None
    status: str
    claims: list[ClaimVerdictOut]
    created_at: datetime


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
        "definition_changed",
        "definition_unknown",
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


class CompatibilityIssue(BaseModel):
    severity: Literal["error", "warning"]
    kind: str
    message: str


class ComparisonOut(BaseModel):
    dataset_id: int
    baseline: RunRef
    candidate: RunRef
    # False when the runs can't be compared (different scoring config, incompatible
    # methodology, or no case with an identical definition). has_regression is then False
    # and makes no claim; see compatibility_issues.
    compatible: bool
    compatibility_issues: list[CompatibilityIssue]
    comparable_cases: int
    has_regression: bool
    regression_reasons: list[str]
    metric_deltas: list[MetricDelta]
    case_counts: dict[str, int]
    case_changes: list[CaseChange]
    config: CompareConfigIn
    # Model-based (estimated) deltas, kept separate and never part of has_regression.
    estimated_metric_deltas: list[MetricDelta] = Field(default_factory=list)
    estimated_note: str | None = None


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
    # RAG (deterministic)
    min_retrieval_recall: float | None = Field(default=None, ge=0, le=1)
    min_mrr: float | None = Field(default=None, ge=0, le=1)
    min_citation_validity: float | None = Field(default=None, ge=0, le=1)
    min_groundedness: float | None = Field(default=None, ge=0, le=1)
    max_unsupported_claims: int | None = Field(default=None, ge=0)
    min_abstention_accuracy: float | None = Field(default=None, ge=0, le=1)
    # RAG (estimated by a local model judge; fails closed if no grades are attached)
    min_estimated_groundedness: float | None = Field(default=None, ge=0, le=1)

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


# ---------------------------------------------------------------- model-based grades


class JudgeInfo(StrictInput):
    model_id: str = Field(min_length=1, max_length=200)
    model_revision: str | None = Field(default=None, max_length=100)
    backend: str = Field(min_length=1, max_length=50)
    prompt_version: str = Field(min_length=1, max_length=50)
    prompt_hash: str = Field(min_length=1, max_length=64)
    settings: dict[str, Any] = Field(default_factory=dict)

    bounded = field_validator("settings")(_bounded_metadata)


class ClaimGradeIn(StrictInput):
    claim: str = Field(min_length=1, max_length=4000)
    verdict: Literal["supported", "unsupported", "error"]
    raw: str | None = Field(default=None, max_length=2000)


class CaseGradeIn(StrictInput):
    case_key: Key
    claims: list[ClaimGradeIn] = Field(max_length=200)
    latency_ms: float | None = Field(default=None, ge=0)


class ModelGradesIn(StrictInput):
    judge: JudgeInfo
    grades: list[CaseGradeIn] = Field(min_length=1, max_length=5000)


# ---------------------------------------------------------------- live chat playground


class ChatSessionCreate(StrictInput):
    dataset_id: int = Field(gt=0)
    prompt_version: str = Field(default="rag-v1", min_length=1, max_length=50)
    title: str = Field(default="New chat", max_length=200)


class ChatSessionOut(OrmOutput):
    id: int
    dataset_id: int
    dataset_name: str | None = None
    title: str
    prompt_version: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ChatMessageIn(StrictInput):
    # Upper bound enforced by the service from settings (default 2000 characters).
    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)
    ]


class ChatMessageOut(OrmOutput):
    id: int
    role: Literal["user", "assistant"]
    content: str
    trace: dict[str, Any] | None = None
    saved_test_case_id: int | None = None
    created_at: datetime


class ChatSessionDetail(ChatSessionOut):
    messages: list[ChatMessageOut] = Field(default_factory=list)


class ChatTurnOut(BaseModel):
    session: ChatSessionOut
    user_message: ChatMessageOut
    assistant_message: ChatMessageOut


class ChatStatusOut(BaseModel):
    enabled: bool
    backend: Literal["off", "fixture", "mlx"]
    model_id: str | None
    model_revision: str | None
    loaded: bool
    busy: bool
    prompt_versions: list[str]
    max_message_chars: int
    history_exchanges: int
    scope: str = "local-only, authenticated API; chat checks are lexical spot checks"


class SaveChatTestCaseIn(StrictInput):
    """Human-labelled test case from a chat turn. Gold evidence is chosen by a person;
    the model's answer is never copied in as an expected output."""

    key: Key
    input: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT)
    relevant_passage_ids: list[PassageId] = Field(default_factory=list, max_length=20)
    expect_abstain: bool = False
    required_terms: list[Term] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _human_label(self) -> SaveChatTestCaseIn:
        if self.expect_abstain and self.relevant_passage_ids:
            raise ValueError("a should-decline case cannot list gold passages")
        if not self.expect_abstain and not self.relevant_passage_ids:
            raise ValueError("select at least one gold passage, or mark the case should-decline")
        return self
