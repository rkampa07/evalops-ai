import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { GateConfig, GateEvaluation, GateResult } from "../api/types";
import { Badge, Card, Empty, ErrorBanner, Loading, PageHeader, Stat } from "../components/ui";
import { dateTime, ms, num, pct } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import { CompatibilityIssues } from "./Compare";
import { RunPicker } from "./RunPicker";

const GATE_FIELDS: { key: keyof GateConfig; label: string; hint: string; needsBaseline?: boolean }[] = [
  { key: "min_pass_rate", label: "Min pass rate", hint: "0–1" },
  { key: "min_mean_token_f1", label: "Min mean token F1", hint: "0–1" },
  { key: "min_schema_pass_rate", label: "Min JSON schema pass rate", hint: "0–1" },
  { key: "min_required_terms_pass_rate", label: "Min required-terms pass rate", hint: "0–1" },
  { key: "max_forbidden_violations", label: "Max forbidden-term violations", hint: "count" },
  { key: "max_p95_latency_ms", label: "Max p95 latency (ms)", hint: "ms" },
  { key: "max_failed_cases", label: "Max failed cases", hint: "count" },
  { key: "max_pass_rate_drop", label: "Max pass-rate drop vs baseline", hint: "0–1", needsBaseline: true },
  { key: "max_regressed_cases", label: "Max regressed cases vs baseline", hint: "count", needsBaseline: true },
  { key: "min_retrieval_recall", label: "Min retrieval recall@k (RAG)", hint: "0–1" },
  { key: "min_citation_validity", label: "Min citation validity (RAG)", hint: "0–1" },
  { key: "min_groundedness", label: "Min groundedness, lexical (RAG)", hint: "0–1" },
  { key: "max_unsupported_claims", label: "Max unsupported claims (RAG)", hint: "count" },
  { key: "min_abstention_accuracy", label: "Min abstention accuracy (RAG)", hint: "0–1" },
  { key: "min_estimated_groundedness", label: "Min groundedness, ESTIMATED by judge", hint: "0–1" },
];

function fmtGateValue(g: GateResult, v: number | null): string {
  if (v === null) return "n/a";
  if (
    g.metric.endsWith("_rate") ||
    g.metric === "pass_rate_drop" ||
    ["retrieval_recall_at_k", "mrr", "groundedness", "abstention_accuracy", "estimated_groundedness"].includes(g.metric)
  )
    return pct(v);
  if (g.metric.endsWith("_ms")) return ms(v);
  return num(v);
}

function GateTable({ evaluation }: { evaluation: GateEvaluation }) {
  return (
    <table className="table">
      <thead>
        <tr>
          <th>Gate</th>
          <th>Threshold</th>
          <th>Actual</th>
          <th>Status</th>
          <th>Detail</th>
        </tr>
      </thead>
      <tbody>
        {evaluation.gates.map((g) => (
          <tr key={g.gate} className={g.status === "fail" ? "row-fail" : ""}>
            <td className="mono">{g.gate}</td>
            <td>
              {g.comparator} {fmtGateValue(g, g.threshold)}
            </td>
            <td>{fmtGateValue(g, g.actual)}</td>
            <td>
              <Badge tone={g.status === "pass" ? "good" : g.status === "fail" ? "bad" : "neutral"}>{g.status}</Badge>
            </td>
            <td className="small muted">{g.message}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CustomGates({ runId, baselineId }: { runId: number; baselineId: number | null }) {
  const [values, setValues] = useState<Record<string, string>>({ min_pass_rate: "0.95", max_forbidden_violations: "0" });
  const [result, setResult] = useState<GateEvaluation>();
  const [error, setError] = useState<string>();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(undefined);
    const gates: GateConfig = {};
    for (const f of GATE_FIELDS) {
      const raw = values[f.key]?.trim();
      if (raw) gates[f.key] = Number(raw);
    }
    try {
      setResult(await api.evaluateGates({ candidate_run_id: runId, baseline_run_id: baselineId, gates }));
    } catch (err) {
      setResult(undefined);
      setError((err as Error).message);
    }
  }

  return (
    <Card title="Custom gate policy">
      <form className="form" onSubmit={submit}>
        <div className="grid-3">
          {GATE_FIELDS.map((f) => (
            <label key={f.key}>
              {f.label} <span className="muted small">({f.hint})</span>
              <input
                type="number"
                step="any"
                min={0}
                value={values[f.key] ?? ""}
                onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                placeholder={f.needsBaseline && !baselineId ? "needs baseline" : "off"}
              />
            </label>
          ))}
        </div>
        <button className="btn btn-primary">Evaluate gates</button>
      </form>
      {error && <ErrorBanner error={error} />}
      {result && (
        <>
          <div className={`verdict ${result.decision === "pass" ? "verdict-good" : "verdict-bad"}`}>Custom policy: {result.decision.toUpperCase()}</div>
          <GateTable evaluation={result} />
        </>
      )}
    </Card>
  );
}

export function Reports() {
  const [params, setParams] = useSearchParams();
  const runId = params.get("run") ?? "";
  const baselineId = params.get("baseline") ?? "";
  const runs = useAsync(() => api.listRuns({ limit: 200 }), []);
  const report = useAsync(
    () => (runId ? api.report(Number(runId), baselineId ? Number(baselineId) : null) : Promise.resolve(undefined)),
    [runId, baselineId],
  );

  function set(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key === "run") next.delete("baseline");
    setParams(next);
  }

  function downloadMarkdown() {
    if (!report.data) return;
    const blob = new Blob([report.data.markdown], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `release-report-run-${report.data.run.id}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  if (runs.loading && !runs.data) return <Loading />;
  if (runs.error) return <ErrorBanner error={runs.error} onRetry={runs.reload} />;
  const all = runs.data?.items ?? [];
  const selected = all.find((r) => String(r.id) === runId);
  const baselines = all.filter((r) => selected && r.dataset_id === selected.dataset_id && r.id !== selected.id);
  const rep = report.data;

  return (
    <>
      <PageHeader title="Release readiness" subtitle="Default policy: pass rate ≥ 90%, zero forbidden-term violations, all JSON valid, and no regressions versus the baseline." />
      <Card>
        <div className="grid-2">
          <RunPicker label="Candidate run" runs={all} value={runId} onChange={(v) => set("run", v)} />
          <RunPicker label="Baseline run (recommended)" runs={baselines} value={baselineId} onChange={(v) => set("baseline", v)} allowNone noneLabel="No baseline (skip regression gates)" />
        </div>
      </Card>

      {!runId ? (
        <Card>
          <Empty>Select a candidate run to generate its release-readiness report.</Empty>
        </Card>
      ) : report.loading && !rep ? (
        <Loading what="Generating report" />
      ) : report.error ? (
        <ErrorBanner error={report.error} onRetry={report.reload} />
      ) : rep ? (
        <>
          <Card
            title={rep.title}
            actions={
              <button className="btn btn-small" onClick={downloadMarkdown}>
                Download Markdown
              </button>
            }
          >
            <div className={`verdict ${rep.readiness === "ready" ? "verdict-good" : "verdict-bad"}`} data-testid="verdict">
              {rep.readiness === "ready" ? "READY FOR RELEASE" : "NOT READY FOR RELEASE"}
            </div>
            <p className="muted small">
              Dataset {rep.dataset_name} · generated {dateTime(rep.generated_at)}
              {rep.baseline && (
                <>
                  {" "}
                  · baseline <Link to={`/runs/${rep.baseline.id}`}>#{rep.baseline.id}</Link> {rep.baseline.model_name} / {rep.baseline.prompt_version}
                </>
              )}
            </p>
            <div className="stat-grid">
              <Stat label="Gates passed" value={`${rep.gate_evaluation.passed_gates}/${rep.gate_evaluation.gates.length}`} tone={rep.gate_evaluation.failed_gates ? "bad" : "good"} />
              <Stat label="Pass rate" value={pct(rep.run.summary.pass_rate)} hint={`${rep.run.summary.passed_cases}/${rep.run.summary.total_cases}`} />
              <Stat
                label="Regressed cases"
                value={rep.comparison?.compatible ? rep.comparison.case_counts.regressed : "n/a"}
                hint={rep.comparison && !rep.comparison.compatible ? "baseline not comparable" : undefined}
                tone={rep.comparison?.compatible ? (rep.comparison.case_counts.regressed ? "bad" : "good") : undefined}
              />
              <Stat label="p95 latency" value={ms(rep.run.summary.latency_p95_ms)} />
            </div>
          </Card>
          <Card title="Gate results">
            <GateTable evaluation={rep.gate_evaluation} />
          </Card>
          {rep.comparison && (
            <Card title="Regression summary" actions={<Link to={`/compare?baseline=${rep.comparison.baseline.id}&candidate=${rep.run.id}`}>Full comparison →</Link>}>
              {!rep.comparison.compatible ? (
                <>
                  <p>
                    <strong>Baseline not comparable</strong>: regression detection was not performed and comparison gates failed
                    closed.
                  </p>
                  <CompatibilityIssues c={rep.comparison} />
                </>
              ) : rep.comparison.has_regression ? (
                <ul>
                  {rep.comparison.regression_reasons.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              ) : (
                <p>No regressions detected within tolerances.</p>
              )}
            </Card>
          )}
          <Card title={`Failing cases (${rep.failing_cases.length})`}>
            {rep.failing_cases.length === 0 ? (
              <Empty>None.</Empty>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>Tags</th>
                    <th>Failed checks</th>
                  </tr>
                </thead>
                <tbody>
                  {rep.failing_cases.map((r) => (
                    <tr key={r.case_key}>
                      <td className="mono">{r.case_key}</td>
                      <td className="small">{r.tags.join(", ")}</td>
                      <td className="small">
                        {r.failures.map((f) => (
                          <div key={f.check}>
                            <strong>{f.check}</strong>: {f.message}
                          </div>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
          <Card title="Notes">
            <ul>
              {rep.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </Card>
          <CustomGates runId={rep.run.id} baselineId={rep.baseline?.id ?? null} />
        </>
      ) : null}
    </>
  );
}
