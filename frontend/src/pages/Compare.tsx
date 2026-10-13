import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { CaseChangeKind, Comparison } from "../api/types";
import { Badge, Card, Empty, ErrorBanner, Loading, PageHeader, PassBadge } from "../components/ui";
import { METRIC_LABELS, formatMetric, metricKind, signed } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import { RunPicker } from "./RunPicker";

const CHANGE_TONE: Record<CaseChangeKind, "good" | "bad" | "warn" | "neutral"> = {
  regressed: "bad",
  fixed: "good",
  f1_drop: "warn",
  unchanged_pass: "neutral",
  unchanged_fail: "warn",
  only_in_baseline: "neutral",
  only_in_candidate: "neutral",
  definition_changed: "warn",
  definition_unknown: "warn",
};
const CHANGE_ORDER: CaseChangeKind[] = [
  "regressed",
  "f1_drop",
  "unchanged_fail",
  "fixed",
  "definition_changed",
  "definition_unknown",
  "only_in_baseline",
  "only_in_candidate",
  "unchanged_pass",
];

export function CompatibilityIssues({ c }: { c: Comparison }) {
  if (c.compatibility_issues.length === 0) return null;
  return (
    <ul className="issues">
      {c.compatibility_issues.map((i) => (
        <li key={i.kind + i.message}>
          <Badge tone={i.severity === "error" ? "bad" : "warn"}>{i.severity}</Badge> {i.message}
        </li>
      ))}
    </ul>
  );
}

export function ComparisonView({ c }: { c: Comparison }) {
  if (!c.compatible) {
    return (
      <Card>
        <div className="verdict verdict-warn" data-testid="compare-verdict">
          Not comparable: no regression verdict
        </div>
        <p>
          These runs can't be compared fairly, so EvalOps makes no claim either way. Re-run the baseline with the same scoring
          config and dataset version as the candidate.
        </p>
        <CompatibilityIssues c={c} />
      </Card>
    );
  }
  const changes = [...c.case_changes].sort((a, b) => CHANGE_ORDER.indexOf(a.change) - CHANGE_ORDER.indexOf(b.change));
  return (
    <>
      <Card>
        <div className={`verdict ${c.has_regression ? "verdict-bad" : "verdict-good"}`} data-testid="compare-verdict">
          {c.has_regression ? "Regression detected" : "No regression detected"}
        </div>
        <p className="muted small">Compared {c.comparable_cases} case(s) whose definitions are identical in both runs.</p>
        <CompatibilityIssues c={c} />
        {c.regression_reasons.length > 0 && (
          <ul>
            {c.regression_reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        <div className="badges">
          {CHANGE_ORDER.map((k) => (
            <Badge key={k} tone={c.case_counts[k] ? CHANGE_TONE[k] : "neutral"}>{`${k.replace(/_/g, " ")}: ${c.case_counts[k]}`}</Badge>
          ))}
        </div>
        <p className="muted small">
          Tolerances: ±{(c.config.metric_tolerance * 100).toFixed(0)} pts on rates, +{(c.config.latency_tolerance_pct * 100).toFixed(0)}% on p95 latency;
          per-case F1 drop flagged above {c.config.case_f1_drop_threshold}.
        </p>
      </Card>

      <Card title="Metric deltas">
        <table className="table">
          <thead>
            <tr>
              <th>Metric</th>
              <th>Baseline #{c.baseline.id}</th>
              <th>Candidate #{c.candidate.id}</th>
              <th>Delta</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {c.metric_deltas.map((d) => (
              <tr key={d.metric}>
                <td>{METRIC_LABELS[d.metric] ?? d.metric}</td>
                <td>{formatMetric(d.metric, d.baseline)}</td>
                <td>{formatMetric(d.metric, d.candidate)}</td>
                <td>{signed(d.delta, metricKind(d.metric))}</td>
                <td>
                  <Badge tone={d.status === "regressed" ? "bad" : d.status === "improved" ? "good" : "neutral"}>{d.status.replace("_", " ")}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="Per-case changes">
        <table className="table">
          <thead>
            <tr>
              <th>Case</th>
              <th>Change</th>
              <th>Baseline</th>
              <th>Candidate</th>
              <th>F1 Δ</th>
              <th>Candidate failures</th>
            </tr>
          </thead>
          <tbody>
            {changes.map((x) => (
              <tr key={x.case_key} className={x.change === "regressed" ? "row-fail" : ""}>
                <td className="mono">{x.case_key}</td>
                <td>
                  <Badge tone={CHANGE_TONE[x.change]}>{x.change.replace(/_/g, " ")}</Badge>
                </td>
                <td><PassBadge passed={x.baseline_passed} /></td>
                <td><PassBadge passed={x.candidate_passed} /></td>
                <td>{x.token_f1_delta === null ? "–" : signed(x.token_f1_delta, "score")}</td>
                <td className="small">{x.candidate_failures.map((f) => f.message).join("; ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}

export function Compare() {
  const [params, setParams] = useSearchParams();
  const baseline = params.get("baseline") ?? "";
  const candidate = params.get("candidate") ?? "";
  const runs = useAsync(() => api.listRuns({ limit: 200 }), []);
  const comparison = useAsync(
    () => (baseline && candidate ? api.compare(Number(baseline), Number(candidate)) : Promise.resolve(undefined)),
    [baseline, candidate],
  );

  function set(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  }

  if (runs.loading && !runs.data) return <Loading />;
  if (runs.error) return <ErrorBanner error={runs.error} onRetry={runs.reload} />;
  const all = runs.data?.items ?? [];
  const candidateRun = all.find((r) => String(r.id) === candidate);
  const baselineRun = all.find((r) => String(r.id) === baseline);
  const datasetId = candidateRun?.dataset_id ?? baselineRun?.dataset_id;
  const sameDataset = datasetId === undefined ? all : all.filter((r) => r.dataset_id === datasetId);

  return (
    <>
      <PageHeader title="Compare runs" subtitle="Baseline (e.g. production) versus candidate (new model or prompt) on the same dataset." />
      <Card>
        <div className="grid-2">
          <RunPicker label="Baseline run" runs={sameDataset.filter((r) => String(r.id) !== candidate)} value={baseline} onChange={(v) => set("baseline", v)} />
          <RunPicker label="Candidate run" runs={sameDataset.filter((r) => String(r.id) !== baseline)} value={candidate} onChange={(v) => set("candidate", v)} />
        </div>
      </Card>
      {!baseline || !candidate ? (
        <Card>
          <Empty>Pick a baseline and a candidate run. Only runs on the same dataset are listed once one is chosen.</Empty>
        </Card>
      ) : comparison.loading && !comparison.data ? (
        <Loading what="Comparing" />
      ) : comparison.error ? (
        <ErrorBanner error={comparison.error} onRetry={comparison.reload} />
      ) : comparison.data ? (
        <>
          <ComparisonView c={comparison.data} />
          <p>
            <Link className="btn btn-primary" to={`/reports?run=${candidate}&baseline=${baseline}`}>
              Release report for candidate →
            </Link>
          </p>
        </>
      ) : null}
    </>
  );
}
