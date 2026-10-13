import type { Comparison, Dataset, Report, Run, RunSummary } from "../api/types";

export const summary = (over: Partial<RunSummary> = {}): RunSummary => ({
  total_cases: 12,
  responded_cases: 12,
  passed_cases: 8,
  failed_cases: 4,
  pass_rate: 8 / 12,
  exact_match_rate: 0.5,
  mean_token_precision: 0.8,
  mean_token_recall: 0.78,
  mean_token_f1: 0.792578,
  required_terms_pass_rate: 0.777778,
  forbidden_terms_pass_rate: 0.666667,
  forbidden_violation_count: 2,
  schema_pass_rate: 1 / 3,
  latency_pass_rate: 1,
  latency_mean_ms: 559,
  latency_p50_ms: 570,
  latency_p95_ms: 694.5,
  latency_max_ms: 700,
  failure_counts: { json_schema: 2 },
  tag_pass_rates: { safety: { cases: 4, pass_rate: 0.75 } },
  ...over,
});

export const run = (id: number, over: Partial<Run> = {}): Run => ({
  id,
  dataset_id: 1,
  dataset_name: "kestrel-support-v1",
  model_name: `model-${id}`,
  prompt_version: `v1.${id}`,
  label: "",
  status: "completed",
  config: { min_token_f1: 0.5, require_exact_match: false, case_sensitive: false, default_max_latency_ms: 2500 },
  summary: summary(),
  pass_rate: 8 / 12,
  methodology_version: "1.1.0",
  dataset_version: 2,
  created_at: "2026-10-13T20:00:00",
  ...over,
});

export const dataset: Dataset = {
  id: 1,
  name: "kestrel-support-v1",
  description: "demo",
  version: 2,
  created_at: "2026-10-13T20:00:00",
  updated_at: "2026-10-13T20:00:00",
  test_case_count: 12,
  run_count: 2,
};

export const comparison: Comparison = {
  dataset_id: 1,
  baseline: { id: 1, model_name: "model-1", prompt_version: "v1.3", label: "", created_at: "2026-10-13T20:00:00" },
  candidate: { id: 2, model_name: "model-2", prompt_version: "v1.4", label: "", created_at: "2026-10-13T20:00:00" },
  compatible: true,
  compatibility_issues: [],
  comparable_cases: 12,
  has_regression: true,
  regression_reasons: ["4 case(s) went from pass to fail", "pass_rate regressed: 0.916667 -> 0.666667"],
  metric_deltas: [
    { metric: "pass_rate", baseline: 0.916667, candidate: 0.666667, delta: -0.25, direction: "higher_is_better", status: "regressed" },
    { metric: "latency_p95_ms", baseline: 1659.5, candidate: 694.5, delta: -965, direction: "lower_is_better", status: "improved" },
  ],
  case_counts: { regressed: 4, fixed: 1, f1_drop: 2, unchanged_pass: 5, unchanged_fail: 0, only_in_baseline: 0, only_in_candidate: 0, definition_changed: 0, definition_unknown: 0 },
  case_changes: [
    {
      case_key: "safety-unauthorized-compensation",
      change: "regressed",
      baseline_passed: true,
      candidate_passed: false,
      baseline_token_f1: null,
      candidate_token_f1: null,
      token_f1_delta: null,
      candidate_failures: [{ check: "forbidden_terms", message: "contains forbidden terms: ['guarantee', '500']" }],
    },
    {
      case_key: "policy-phone-hours",
      change: "fixed",
      baseline_passed: false,
      candidate_passed: true,
      baseline_token_f1: 1,
      candidate_token_f1: 1,
      token_f1_delta: 0,
      candidate_failures: [],
    },
  ],
  config: { metric_tolerance: 0.02, latency_tolerance_pct: 0.2, case_f1_drop_threshold: 0.15 },
};

export const incomparable: Comparison = {
  ...comparison,
  compatible: false,
  compatibility_issues: [
    {
      severity: "error",
      kind: "scoring_config",
      message: "runs were scored with different scoring configs (min_token_f1: 0.3 -> 0.6)",
    },
  ],
  comparable_cases: 0,
  has_regression: false,
  regression_reasons: [],
  metric_deltas: [],
  case_changes: [],
};

export const report = (readiness: "ready" | "not_ready"): Report => ({
  title: "Release readiness: model-2 / v1.4",
  generated_at: "2026-10-13T21:00:00+00:00",
  run: run(2),
  dataset_name: "kestrel-support-v1",
  baseline: comparison.baseline,
  readiness,
  gate_policy: { min_pass_rate: 0.9 },
  gate_evaluation: {
    candidate_run_id: 2,
    baseline_run_id: 1,
    decision: readiness === "ready" ? "pass" : "fail",
    passed_gates: readiness === "ready" ? 1 : 0,
    failed_gates: readiness === "ready" ? 0 : 1,
    skipped_gates: 0,
    regression_detected: readiness !== "ready",
    gates: [
      {
        gate: "min_pass_rate",
        metric: "pass_rate",
        comparator: ">=",
        threshold: 0.9,
        actual: readiness === "ready" ? 1 : 0.666667,
        status: readiness === "ready" ? "pass" : "fail",
        message: "pass_rate check",
      },
    ],
  },
  comparison,
  failing_cases: [],
  notes: ["Metrics are lexical and structural checks."],
  markdown: "# Release readiness\n",
});

/** Fetch mock keyed by exact "METHOD /path" (query string ignored); unknown routes return 404. */
export function mockFetch(routes: Record<string, unknown>) {
  const calls: string[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const key = `${init?.method ?? "GET"} ${url.pathname}`;
    calls.push(`${key}${url.search}`);
    const body = routes[key];
    if (body === undefined) {
      return new Response(JSON.stringify({ error: { code: "not_found", message: `no mock for ${key}` } }), { status: 404 });
    }
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}
