// Mirrors backend/evalops/schemas.py

export interface Dataset {
  id: number;
  name: string;
  description: string;
  version: number;
  created_at: string;
  updated_at: string;
  test_case_count: number;
  run_count: number;
  passage_count: number;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface AuthSession {
  authenticated: boolean;
  mode: "oidc" | "development";
  user?: { id: number; issuer: string; subject: string } | null;
  csrf_token?: string | null;
  workspaces?: WorkspaceSummary[];
  active_workspace_id?: number | null;
}

export interface WorkspaceSummary {
  id: number;
  name: string;
  role: "owner" | "admin" | "editor" | "viewer";
  is_legacy: boolean;
}

export interface Passage {
  passage_id: string;
  title: string;
  text: string;
  created_at: string;
  updated_at: string;
}

export interface TestCaseInput {
  key: string;
  input: string;
  expected_output?: string | null;
  required_terms?: string[];
  forbidden_terms?: string[];
  json_schema?: Record<string, unknown> | null;
  max_latency_ms?: number | null;
  tags?: string[];
  relevant_passage_ids?: string[];
  expect_abstain?: boolean;
}

export interface TestCase extends Required<TestCaseInput> {
  id: number;
  dataset_id: number;
  version: number;
  content_hash: string;
  created_at: string;
  updated_at: string;
}

export interface EvalConfig {
  min_token_f1: number;
  require_exact_match: boolean;
  case_sensitive: boolean;
  default_max_latency_ms: number | null;
  min_groundedness?: number;
}

export interface Failure {
  check: string;
  message: string;
}

export interface CaseMetrics {
  responded: boolean;
  exact_match?: boolean;
  token_precision?: number;
  token_recall?: number;
  token_f1?: number;
  required_terms_missing?: string[];
  required_terms_passed?: boolean;
  forbidden_terms_found?: string[];
  forbidden_terms_passed?: boolean;
  json_parsed?: boolean;
  json_valid?: boolean;
  schema_errors?: string[];
  latency_ms?: number | null;
  latency_limit_ms?: number;
  latency_passed?: boolean;
  // RAG (deterministic)
  rag?: boolean;
  retrieval_recall?: number;
  reciprocal_rank?: number;
  abstained?: boolean;
  abstention_correct?: boolean;
  citations?: string[];
  citations_nonexistent?: string[];
  citations_not_retrieved?: string[];
  claims?: ClaimCheck[];
  groundedness?: number | null;
  unsupported_claims?: string[];
}

export interface ClaimCheck {
  text: string;
  citations: string[];
  supported: boolean;
  passage_id: string | null;
  coverage: number;
  missing_numbers: string[];
}

export interface JudgeInfo {
  model_id: string;
  model_revision?: string | null;
  backend: string;
  prompt_version: string;
  prompt_hash: string;
  settings: Record<string, unknown>;
}

export interface ModelGrades {
  label: "estimated";
  judge: JudgeInfo;
  latency_ms?: number | null;
  claims: { claim: string; verdict: "supported" | "unsupported" | "error"; raw?: string | null }[];
}

export interface ModelGradedSummary {
  label: "estimated";
  judge: JudgeInfo;
  graded_cases: number;
  claims_total: number;
  claims_supported: number;
  claims_error: number;
  estimated_groundedness: number | null;
  estimated_unsupported_claims: number;
  heuristic_judge_agreement: number | null;
}

export interface Result {
  id: number;
  case_key: string;
  test_case_id: number | null;
  output: string | null;
  latency_ms: number | null;
  passed: boolean;
  metrics: CaseMetrics;
  failures: Failure[];
  tags: string[];
  retrieved_passage_ids?: string[] | null;
  generation?: Record<string, unknown> | null;
  model_grades?: ModelGrades | null;
  case_version?: number | null;
  case_hash?: string | null;
  case_snapshot?: Record<string, unknown> | null;
}

export interface RunSummary {
  total_cases: number;
  responded_cases: number;
  passed_cases: number;
  failed_cases: number;
  pass_rate: number | null;
  exact_match_rate: number | null;
  mean_token_precision: number | null;
  mean_token_recall: number | null;
  mean_token_f1: number | null;
  required_terms_pass_rate: number | null;
  forbidden_terms_pass_rate: number | null;
  forbidden_violation_count: number;
  schema_pass_rate: number | null;
  latency_pass_rate: number | null;
  latency_mean_ms: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  latency_max_ms: number | null;
  failure_counts: Record<string, number>;
  tag_pass_rates: Record<string, { cases: number; pass_rate: number | null }>;
  rag_cases?: number;
  retrieval_recall_at_k?: number | null;
  mrr?: number | null;
  citation_validity_rate?: number | null;
  citation_support_rate?: number | null;
  groundedness?: number | null;
  unsupported_claim_count?: number;
  abstention_accuracy?: number | null;
}

export interface Run {
  id: number;
  dataset_id: number;
  dataset_name: string | null;
  model_name: string;
  prompt_version: string;
  label: string;
  status: string;
  config: EvalConfig;
  summary: RunSummary;
  pass_rate: number | null;
  methodology_version: string;
  dataset_version: number | null;
  knowledge_base_hash?: string | null;
  generation?: Record<string, unknown> | null;
  model_graded?: ModelGradedSummary | null;
  created_at: string;
}

export interface RunDetail extends Run {
  results: Result[];
}

export interface RunList {
  items: Run[];
  total: number;
  limit: number;
  offset: number;
}

export interface ResponseInput {
  case_key: string;
  output: string;
  latency_ms?: number | null;
}

export interface RunCreate {
  dataset_id: number;
  model_name: string;
  prompt_version: string;
  label?: string;
  config?: Partial<EvalConfig>;
  responses: ResponseInput[];
}

export type EvaluationJobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface JobProgress {
  completed: number;
  total: number;
  fraction: number | null;
}

export interface JobError {
  code: string;
  message: string;
}

export interface EvaluationJob {
  job_id: number;
  status: EvaluationJobStatus;
  status_url: string;
  dataset_id: number;
  dataset_version: number;
  scoring_input_fingerprint: string;
  knowledge_base_hash: string | null;
  progress: JobProgress;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  heartbeat_at: string | null;
  finished_at: string | null;
  attempt_count: number;
  lease_owner: string | null;
  lease_expires_at: string | null;
  cancellation_requested_at: string | null;
  error: JobError | null;
  run_id: number | null;
  retry_of_job_id: number | null;
  replayed: boolean;
}

export type AssessmentStatus = EvaluationJobStatus;
export type CitationAssessmentStatus = AssessmentStatus;

export type CitationPredictionStatus =
  | "correct"
  | "wrong_passage"
  | "unrelated"
  | "not_retrieved"
  | "partial"
  | "not_applicable"
  | "missing_citation"
  | "invalid_citation"
  | "unresolved";

export interface CitationAssessmentCreate {
  evaluator: "citation";
  citation_expected: boolean;
}

export interface ClaimAssessmentCreate {
  evaluator: "claim";
}

export type AssessmentCreate = CitationAssessmentCreate | ClaimAssessmentCreate;

export interface CitationAssessmentJob {
  assessment_id: number;
  evaluator: "citation";
  status: CitationAssessmentStatus;
  status_url: string;
  result_url: string;
  run_id: number;
  result_id: number;
  progress: JobProgress;
  evaluator_version: string;
  predictor_sha256: string;
  runner_sha256: string;
  config_digest: string;
  input_fingerprint: string;
  knowledge_base_hash: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  heartbeat_at: string | null;
  finished_at: string | null;
  attempt_count: number;
  cancellation_requested: boolean;
  error: JobError | null;
  replayed: boolean;
}

export interface CitationDecision {
  citation_id: string | null;
  status: CitationPredictionStatus;
  reason_code: string;
  reason: string;
  component_coverage: number[];
}

export interface CitationAssessmentResult {
  assessment_id: number;
  run_id: number;
  result_id: number;
  case_key: string;
  evaluator_version: string;
  predictor_sha256: string;
  runner_sha256: string;
  config_digest: string;
  input_fingerprint: string;
  knowledge_base_hash: string | null;
  status: CitationPredictionStatus;
  reason_code: string;
  reason: string;
  per_citation: CitationDecision[];
  created_at: string;
}

export interface ClaimAssessmentJob {
  assessment_id: number;
  evaluator: "claim";
  status: CitationAssessmentStatus;
  status_url: string;
  result_url: string;
  run_id: number;
  result_id: number;
  progress: JobProgress;
  evaluator_version: string;
  source_sha256: string;
  config_digest: string;
  model_id: string;
  model_revision: string;
  input_fingerprint: string;
  knowledge_base_hash: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  heartbeat_at: string | null;
  finished_at: string | null;
  attempt_count: number;
  cancellation_requested: boolean;
  error: JobError | null;
  replayed: boolean;
}

export type ClaimLabel =
  | "supported"
  | "contradicted"
  | "insufficient_evidence"
  | "needs_human_review";

export interface ClaimVerdict {
  claim_text: string;
  label: ClaimLabel;
  abstained: boolean;
  reason: string;
  diagnostic_branch: string;
  deciding_evidence: Record<string, unknown>[];
  guards: string[];
  config: Record<string, unknown>;
  model: Record<string, unknown>;
  uncalibrated_scores: Record<string, unknown>[];
  ignored_contradictions: Record<string, unknown>[];
}

export interface ClaimAssessmentResult {
  assessment_id: number;
  run_id: number;
  result_id: number;
  case_key: string;
  evaluator_version: string;
  source_sha256: string;
  config_digest: string;
  model_id: string;
  model_revision: string;
  input_fingerprint: string;
  knowledge_base_hash: string | null;
  status: string;
  claims: ClaimVerdict[];
  created_at: string;
}

export type AssessmentJob = CitationAssessmentJob | ClaimAssessmentJob;
export type AssessmentResult = CitationAssessmentResult | ClaimAssessmentResult;

export interface RunRef {
  id: number;
  model_name: string;
  prompt_version: string;
  label: string;
  created_at: string;
}

export type MetricStatus = "improved" | "regressed" | "unchanged" | "not_applicable";

export interface MetricDelta {
  metric: string;
  baseline: number | null;
  candidate: number | null;
  delta: number | null;
  direction: "higher_is_better" | "lower_is_better";
  status: MetricStatus;
}

export type CaseChangeKind =
  | "regressed"
  | "fixed"
  | "f1_drop"
  | "unchanged_pass"
  | "unchanged_fail"
  | "only_in_baseline"
  | "only_in_candidate"
  | "definition_changed"
  | "definition_unknown";

export interface CaseChange {
  case_key: string;
  change: CaseChangeKind;
  baseline_passed: boolean | null;
  candidate_passed: boolean | null;
  baseline_token_f1: number | null;
  candidate_token_f1: number | null;
  token_f1_delta: number | null;
  candidate_failures: Failure[];
}

export interface CompatibilityIssue {
  severity: "error" | "warning";
  kind: string;
  message: string;
}

export interface Comparison {
  dataset_id: number;
  baseline: RunRef;
  candidate: RunRef;
  /** False when the runs can't be compared; has_regression then makes no claim. */
  compatible: boolean;
  compatibility_issues: CompatibilityIssue[];
  comparable_cases: number;
  has_regression: boolean;
  regression_reasons: string[];
  metric_deltas: MetricDelta[];
  case_counts: Record<CaseChangeKind, number>;
  case_changes: CaseChange[];
  config: { metric_tolerance: number; latency_tolerance_pct: number; case_f1_drop_threshold: number };
  estimated_metric_deltas?: MetricDelta[];
  estimated_note?: string | null;
}

export interface GateConfig {
  min_pass_rate?: number;
  min_mean_token_f1?: number;
  min_exact_match_rate?: number;
  min_required_terms_pass_rate?: number;
  min_schema_pass_rate?: number;
  min_latency_pass_rate?: number;
  max_forbidden_violations?: number;
  max_p95_latency_ms?: number;
  max_failed_cases?: number;
  max_pass_rate_drop?: number;
  max_regressed_cases?: number;
  min_retrieval_recall?: number;
  min_mrr?: number;
  min_citation_validity?: number;
  min_groundedness?: number;
  max_unsupported_claims?: number;
  min_abstention_accuracy?: number;
  min_estimated_groundedness?: number;
}

export interface GateResult {
  gate: string;
  metric: string;
  comparator: ">=" | "<=";
  threshold: number;
  actual: number | null;
  status: "pass" | "fail" | "skipped";
  message: string;
}

export interface GateEvaluation {
  candidate_run_id: number;
  baseline_run_id: number | null;
  decision: "pass" | "fail";
  passed_gates: number;
  failed_gates: number;
  skipped_gates: number;
  gates: GateResult[];
  regression_detected: boolean | null;
}

export interface Report {
  title: string;
  generated_at: string;
  run: Run;
  dataset_name: string;
  baseline: RunRef | null;
  readiness: "ready" | "not_ready";
  gate_policy: Record<string, number>;
  gate_evaluation: GateEvaluation;
  comparison: Comparison | null;
  failing_cases: Result[];
  notes: string[];
  markdown: string;
}

// ---------------------------------------------------------------- live chat playground

export interface ChatStatus {
  enabled: boolean;
  backend: "off" | "fixture" | "mlx";
  model_id: string | null;
  model_revision: string | null;
  loaded: boolean;
  busy: boolean;
  prompt_versions: string[];
  max_message_chars: number;
  history_exchanges: number;
  scope: string;
}

export interface ChatSession {
  id: number;
  dataset_id: number;
  dataset_name: string | null;
  title: string;
  prompt_version: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface TracePassage {
  passage_id: string;
  title: string;
  text: string;
  bm25_score: number;
}

export interface LiveCheck {
  label: string;
  methodology_version: string;
  passed_lexical_checks: boolean;
  failures: Failure[];
  abstained: boolean;
  abstention: string;
  citations: string[];
  citations_valid: number;
  citations_nonexistent: string[];
  citations_not_retrieved: string[];
  citation_pairs_total: number;
  citation_pairs_supporting: number;
  claims: ClaimCheck[];
  groundedness: number | null;
  unsupported_claims: string[];
  min_groundedness: number;
}

export type DiagnosticState = "potential_contradiction" | "citation_concern" | "needs_human_review";

export interface ChatDiagnosticWarning {
  state: DiagnosticState;
  kind: string;
  claim: string;
  evidence: { passage_id: string; text: string } | null;
  reason: string;
  heuristic: true;
}

export interface ChatDiagnostics {
  version: string;
  label: string;
  warnings: ChatDiagnosticWarning[];
  claims_checked: number;
  note?: string;
  computed_on_read?: boolean;
}

export interface ChatTrace {
  retrieval: {
    query: string;
    used_previous_turn: boolean;
    retriever: string;
    k: number;
    passages: TracePassage[];
  };
  prompt: {
    template_version: string;
    chat_prompt_version: string;
    hash: string;
    history_exchanges_included: number;
    history_truncated: boolean;
    messages: { role: string; content: string }[];
  };
  model: Record<string, unknown>;
  settings: Record<string, unknown>;
  generation: Record<string, unknown>;
  live_check: LiveCheck;
  /** Chat-only heuristic warnings; absent only on very old traces. */
  diagnostics?: ChatDiagnostics;
  judge: { status: string };
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  trace: ChatTrace | null;
  saved_test_case_id: number | null;
  created_at: string;
}

export interface ChatSessionDetail extends ChatSession {
  messages: ChatMessage[];
}

export interface ChatTurn {
  session: ChatSession;
  user_message: ChatMessage;
  assistant_message: ChatMessage;
}

export interface SaveChatTestCaseInput {
  key: string;
  input?: string;
  relevant_passage_ids: string[];
  expect_abstain: boolean;
  required_terms?: string[];
}
