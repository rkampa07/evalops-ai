import { Fragment, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { AssessmentStatus, Result } from "../api/types";
import { AssessmentResultCard } from "../components/AssessmentResults";
import { ClaimsTable, EstimatedCard, GenerationCard, RagEvidenceCard } from "../components/RagPanels";
import {
  Badge,
  Card,
  Empty,
  ErrorBanner,
  Loading,
  Meter,
  PageHeader,
  PaginationControls,
  PassBadge,
  Stat,
} from "../components/ui";
import { useAssessment } from "../hooks/useAssessment";
import { dateTime, ms, num, pct } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const PAGE_SIZE = 50;

function assessmentStatusTone(
  status: AssessmentStatus,
): "good" | "bad" | "warn" | "info" | "neutral" {
  if (status === "succeeded") return "good";
  if (status === "failed") return "bad";
  if (status === "cancelled") return "neutral";
  if (status === "running") return "info";
  return "warn";
}

function ResultAssessments({ runId, result }: { runId: number; result: Result }) {
  const defaultCitationExpected =
    result.case_snapshot?.expect_abstain === true ? false : !result.metrics.abstained;
  const [citationExpected, setCitationExpected] = useState<boolean>(defaultCitationExpected);
  const assessment = useAssessment(runId, result.id);
  const hasOutput = result.output !== null && result.output !== undefined && result.metrics.responded;
  const hasRetrievalMetadata = Array.isArray(result.retrieved_passage_ids);
  const { job } = assessment;

  return (
    <div className="claims" aria-label={`Frozen assessments for ${result.case_key}`}>
      <p className="muted small">
        Separate, opt-in frozen assessments (<code>citation-evaluator-v4</code> and{" "}
        <code>semantic-aggregate-v12</code>). These do not modify deterministic lexical scores, run
        summaries, comparisons, or release gates.
      </p>
      {!hasOutput ? (
        <p className="muted small">
          Frozen assessments are unavailable because no response text was submitted for this case.
        </p>
      ) : (
        <>
          <div className="row">
            <label className="checkbox small">
              <input
                type="checkbox"
                checked={citationExpected}
                disabled={assessment.isActive}
                onChange={(e) => setCitationExpected(e.target.checked)}
              />
              Citation expected
            </label>
            <button
              type="button"
              className="btn btn-small"
              disabled={assessment.isActive}
              onClick={() =>
                void assessment.submit({
                  evaluator: "citation",
                  citation_expected: citationExpected,
                })
              }
            >
              Run citation assessment
            </button>
            <button
              type="button"
              className="btn btn-small"
              disabled={assessment.isActive || !hasRetrievalMetadata}
              onClick={() => void assessment.submit({ evaluator: "claim" })}
            >
              Run claim assessment
            </button>
          </div>
          {!hasRetrievalMetadata && (
            <p className="muted small">
              Claim assessment requires recorded retrieval metadata (<code>retrieved_passage_ids</code>).
            </p>
          )}
        </>
      )}

      {assessment.submitting && !job && (
        <p className="small muted" role="status">
          Submitting assessment…
        </p>
      )}

      {job && (
        <div className="row small" role="status" aria-live="polite">
          <Badge tone={assessmentStatusTone(job.status)}>{job.status}</Badge>
          <span className="mono">
            {job.evaluator} #{job.assessment_id}
          </span>
          <span className="muted">
            {job.progress.completed} of {job.progress.total} completed
          </span>
          {job.status === "queued" && !job.cancellation_requested && (
            <span className="muted">Queued for worker…</span>
          )}
          {job.status === "running" && !job.cancellation_requested && (
            <span className="muted">Running assessment…</span>
          )}
          {job.cancellation_requested && (job.status === "queued" || job.status === "running") && (
            <span className="muted">Cancellation requested…</span>
          )}
          {job.status === "cancelled" && <span className="muted">Assessment was cancelled.</span>}
          {job.status === "succeeded" && assessment.loadingResult && (
            <span className="muted">Loading assessment result…</span>
          )}
          {(job.status === "queued" || job.status === "running") && (
            <button
              type="button"
              className="btn btn-small"
              disabled={assessment.cancelling || job.cancellation_requested}
              onClick={() => void assessment.cancel()}
            >
              {assessment.cancelling ? "Cancelling…" : "Cancel assessment"}
            </button>
          )}
        </div>
      )}

      {assessment.error && <ErrorBanner error={assessment.error} />}
      {assessment.pollError && (
        <ErrorBanner error={`${assessment.pollError} Status polling will retry automatically.`} />
      )}
      {assessment.result && <AssessmentResultCard result={assessment.result} />}
    </div>
  );
}

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
      {m.rag && m.groundedness !== undefined && m.groundedness !== null && (
        <Badge tone={r.failures.some((f) => f.check === "groundedness") ? "bad" : "good"}>{`grounded ${pct(m.groundedness, 0)}`}</Badge>
      )}
      {m.rag && m.citations !== undefined && (
        <Badge tone={r.failures.some((f) => f.check === "citations") ? "bad" : "good"}>citations</Badge>
      )}
      {m.rag && <Badge tone={m.abstention_correct ? "good" : "bad"}>{m.abstained ? "declined" : "answered"}</Badge>}
    </div>
  );
}

export function RunDetail() {
  const id = Number(useParams().id);
  const [offset, setOffset] = useState(0);
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [expanded, setExpanded] = useState<string>();
  const runQuery = useAsync(() => api.getRunSummary(id), [id]);
  const resultsQuery = useAsync(
    () => api.listRunResults(id, { limit: PAGE_SIZE, offset, failed_only: onlyFailed }),
    [id, offset, onlyFailed],
  );

  useEffect(() => {
    setOffset(0);
    setOnlyFailed(false);
    setExpanded(undefined);
  }, [id]);

  function reload() {
    runQuery.reload();
    resultsQuery.reload();
  }

  if (runQuery.loading || resultsQuery.loading) return <Loading />;
  const error = runQuery.error ?? resultsQuery.error;
  if (error) return <ErrorBanner error={error} onRetry={reload} />;
  if (!runQuery.data || !resultsQuery.data) return null;
  const run = runQuery.data;
  const s = run.summary;
  const results = resultsQuery.data.items;

  return (
    <>
      <PageHeader
        title={`Run #${run.id}`}
        subtitle={
          <>
            <strong>{run.model_name}</strong> · prompt {run.prompt_version} · dataset{" "}
            <Link to={`/datasets/${run.dataset_id}`}>{run.dataset_name}</Link> · {dateTime(run.created_at)}
            {run.label && <> · {run.label}</>}
            <br />
            <span className="small">
              dataset version {run.dataset_version ?? "unknown (pre-versioning run)"} · scoring methodology {run.methodology_version}
            </span>
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

      {(s.rag_cases ?? 0) > 0 && (
        <div className="grid-2">
          <RagEvidenceCard s={s} />
          {run.model_graded ? (
            <EstimatedCard mg={run.model_graded} />
          ) : (
            <Card title="Model-based scores">
              <p className="muted small">No model-judge grades attached. Deterministic metrics above are complete on their own.</p>
            </Card>
          )}
        </div>
      )}
      {run.generation && <GenerationCard generation={run.generation} />}

      <Card
        title={`Results (${resultsQuery.data.total})`}
        actions={
          <label className="checkbox small">
            <input
              type="checkbox"
              checked={onlyFailed}
              onChange={(e) => {
                setOffset(0);
                setOnlyFailed(e.target.checked);
              }}
            />
            Failed only
          </label>
        }
      >
          {resultsQuery.data.total === 0 ? (
            <Empty>{onlyFailed ? "No failed results." : "This run has no results."}</Empty>
          ) : (
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
                            {r.metrics.rag && (
                              <>
                                <dt>Evidence</dt>
                                <dd><ClaimsTable r={r} /></dd>
                              </>
                            )}
                            <dt>Assessments</dt>
                            <dd><ResultAssessments runId={run.id} result={r} /></dd>
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
          )}
          <PaginationControls
            limit={resultsQuery.data.limit}
            offset={offset}
            total={resultsQuery.data.total}
            loading={resultsQuery.loading}
            onOffsetChange={setOffset}
          />
      </Card>
    </>
  );
}
