export function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "n/a" : `${(value * 100).toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined) return "n/a";
  return Number.isInteger(value) ? String(value) : value.toFixed(digits);
}

export function ms(value: number | null | undefined): string {
  return value === null || value === undefined ? "n/a" : `${Math.round(value)}\u00a0ms`;
}

export function signed(value: number | null | undefined, kind: "rate" | "ms" | "score"): string {
  if (value === null || value === undefined) return "n/a";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "±";
  const abs = Math.abs(value);
  if (kind === "rate") return `${sign}${(abs * 100).toFixed(1)}\u00a0pts`;
  if (kind === "ms") return `${sign}${Math.round(abs)}\u00a0ms`;
  return `${sign}${abs.toFixed(3)}`;
}

export function dateTime(iso: string): string {
  // Backend stores UTC; SQLite drops the offset, so treat naive timestamps as UTC.
  const normalized = /[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(normalized);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export const METRIC_LABELS: Record<string, string> = {
  pass_rate: "Pass rate",
  exact_match_rate: "Exact match rate",
  mean_token_f1: "Mean token F1",
  required_terms_pass_rate: "Required terms pass rate",
  forbidden_terms_pass_rate: "Forbidden terms pass rate",
  schema_pass_rate: "JSON schema pass rate",
  latency_pass_rate: "Latency pass rate",
  latency_p95_ms: "Latency p95",
  retrieval_recall_at_k: "Retrieval recall@k",
  citation_validity_rate: "Citation validity",
  groundedness: "Groundedness (lexical)",
  abstention_accuracy: "Abstention accuracy",
  estimated_groundedness: "Groundedness (estimated by judge)",
};

export function metricKind(metric: string): "rate" | "ms" | "score" {
  if (metric.endsWith("_ms")) return "ms";
  if (metric.includes("f1")) return "score";
  return "rate";
}

export function formatMetric(metric: string, value: number | null | undefined): string {
  const kind = metricKind(metric);
  return kind === "rate" ? pct(value) : kind === "ms" ? ms(value) : num(value);
}

export function runLabel(run: { id: number; model_name: string; prompt_version: string; label?: string }): string {
  return `#${run.id} · ${run.model_name} · ${run.prompt_version}${run.label ? ` — ${run.label}` : ""}`;
}
