// Mirrors backend/evalops/schemas.py

export interface Dataset {
  id: number;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  test_case_count: number;
  run_count: number;
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
}

export interface TestCase extends Required<TestCaseInput> {
  id: number;
  dataset_id: number;
  created_at: string;
  updated_at: string;
}

export interface EvalConfig {
  min_token_f1: number;
  require_exact_match: boolean;
  case_sensitive: boolean;
  default_max_latency_ms: number | null;
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
  | "only_in_candidate";

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

export interface Comparison {
  dataset_id: number;
  baseline: RunRef;
  candidate: RunRef;
  has_regression: boolean;
  regression_reasons: string[];
  metric_deltas: MetricDelta[];
  case_counts: Record<CaseChangeKind, number>;
  case_changes: CaseChange[];
  config: { metric_tolerance: number; latency_tolerance_pct: number; case_f1_drop_threshold: number };
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
