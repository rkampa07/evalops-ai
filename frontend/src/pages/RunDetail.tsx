import { Fragment, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Result } from "../api/types";
import { Badge, Card, ErrorBanner, Loading, Meter, PageHeader, PassBadge, Stat } from "../components/ui";
import { dateTime, ms, num, pct } from "../lib/format";
import { useAsync } from "../lib/useAsync";

function ResultChecks({ r }: { r: Result }) {
  const m = r.metrics;
  if (!m.responded) return <Badge tone="bad">no response</Badge>;
  return (
    <div className="badges">
      {m.token_f1 !== undefined && <Badge tone={r.failures.some((f) => f.check === "token_f1") ? "bad" : "neutral"}>F1 {num(m.token_f1, 2)}</Badge>}
      {m.required_terms_passed !== undefined && <Badge tone={m.required_terms_passed ? "good" : "bad"}>required</Badge>}
      {m.forbidden_terms_passed !== undefined && <Badge tone={m.forbidden_terms_passed ? "good" : "bad"}>forbidden</Badge>}
      {m.json_valid !== undefined && <Badge tone={m.json_valid ? "good" : "bad"}>schema</Badge>}
      {m.latency_passed !== undefined && <Badge tone={m.latency_passed ? "good" : "bad"}>latency</Badge>}
    </div>
  );
}

export function RunDetail() {
  const id = Number(useParams().id);
  const { data: run, error, loading, reload } = useAsync(() => api.getRun(id), [id]);
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [expanded, setExpanded] = useState<string>();

  if (loading && !run) return <Loading />;
  if (error) return <ErrorBanner error={error} onRetry={reload} />;
  if (!run) return null;
  const s = run.summary;
  const results = onlyFailed ? run.results.filter((r) => !r.passed) : run.results;

  return (
    <>
      <PageHeader
        title={`Run #${run.id}`}
        subtitle={
          <>
            <strong>{run.model_name}</strong> · prompt {run.prompt_version} · dataset{" "}
            <Link to={`/datasets/${run.dataset_id}`}>{run.dataset_name}</Link> · {dateTime(run.created_at)}
            {run.label && <> · {run.label}</>}
          </>
        }
        actions={
          <>
            <Link className="btn" to={`/compare?candidate=${run.id}`}>
              Compare
            </Link>
            <Link className="btn btn-primary" to={`/reports?run=${run.id}`}>
              Release report
            </Link>
          </>
        }
      />
      <div className="stat-grid">
        <Stat label="Pass rate" value={pct(s.pass_rate)} hint={`${s.passed_cases}/${s.total_cases} cases`} tone={s.pass_rate !== null && s.pass_rate >= 0.9 ? "good" : "bad"} />
        <Stat label="Mean token F1" value={num(s.mean_token_f1)} hint={`P ${num(s.mean_token_precision)} · R ${num(s.mean_token_recall)}`} />
        <Stat label="Forbidden-term violations" value={s.forbidden_violation_count} tone={s.forbidden_violation_count ? "bad" : "good"} />
        <Stat label="Latency p50 / p95" value={`${ms(s.latency_p50_ms)}`} hint={`p95 ${ms(s.latency_p95_ms)} · max ${ms(s.latency_max_ms)}`} />
      </div>

      <div className="grid-2">
        <Card title="Check pass rates">
          <table className="table compact">
            <tbody>
              <tr><td>Exact match</td><td className="w-meter"><Meter value={s.exact_match_rate} label="exact match" /></td></tr>
              <tr><td>Required terms</td><td className="w-meter"><Meter value={s.required_terms_pass_rate} label="required terms" /></td></tr>
              <tr><td>Forbidden terms</td><td className="w-meter"><Meter value={s.forbidden_terms_pass_rate} label="forbidden terms" /></td></tr>
              <tr><td>JSON schema</td><td className="w-meter"><Meter value={s.schema_pass_rate} label="json schema" /></td></tr>
              <tr><td>Latency</td><td className="w-meter"><Meter value={s.latency_pass_rate} label="latency" /></td></tr>
            </tbody>
          </table>
          <p className="muted small">
            Scoring policy: min F1 {run.config.min_token_f1}
            {run.config.require_exact_match && ", exact match required"}
            {run.config.default_max_latency_ms && `, default latency limit ${run.config.default_max_latency_ms} ms`}.
          </p>
        </Card>
        <Card title="By tag">
          <table className="table compact">
            <tbody>
              {Object.entries(s.tag_pass_rates).map(([tag, t]) => (
                <tr key={tag}>
                  <td>
                    {tag} <span className="muted small">({t.cases})</span>
                  </td>
                  <td className="w-meter"><Meter value={t.pass_rate} label={`tag ${tag}`} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {Object.keys(s.failure_counts).length > 0 && (
            <p className="small">
              Failures by check:{" "}
              {Object.entries(s.failure_counts).map(([k, v]) => (
                <Badge key={k} tone="bad">{`${k} ×${v}`}</Badge>
              ))}
            </p>
          )}
        </Card>
      </div>

      <Card
        title={`Results (${results.length})`}
        actions={
          <label className="checkbox small">
            <input type="checkbox" checked={onlyFailed} onChange={(e) => setOnlyFailed(e.target.checked)} />
            Failed only
          </label>
        }
      >
        <table className="table">
          <thead>
            <tr>
              <th>Case</th>
              <th>Status</th>
              <th>Checks</th>
              <th>Latency</th>
              <th>Failure reasons</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r) => (
              <Fragment key={r.case_key}>
                <tr className={r.passed ? "" : "row-fail"}>
                  <td className="mono">
                    <button className="link" onClick={() => setExpanded(expanded === r.case_key ? undefined : r.case_key)} aria-expanded={expanded === r.case_key}>
                      {r.case_key}
                    </button>
                  </td>
                  <td><PassBadge passed={r.passed} /></td>
                  <td><ResultChecks r={r} /></td>
                  <td>{ms(r.latency_ms)}</td>
                  <td className="small">{r.failures.map((f) => f.message).join("; ")}</td>
                </tr>
                {expanded === r.case_key && (
                  <tr className="detail-row">
                    <td colSpan={5}>
                      <dl className="kv">
                        <dt>Output</dt>
                        <dd><pre>{r.output ?? "(no response submitted)"}</pre></dd>
                        <dt>Metrics</dt>
                        <dd><pre>{JSON.stringify(r.metrics, null, 2)}</pre></dd>
                      </dl>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}
