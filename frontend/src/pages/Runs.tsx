import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { Card, Empty, ErrorBanner, Loading, Meter, PageHeader } from "../components/ui";
import { dateTime, ms, num } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const PAGE = 25;

export function Runs() {
  const [params, setParams] = useSearchParams();
  const datasetId = params.get("dataset") ?? "";
  const model = params.get("model") ?? "";
  const prompt = params.get("prompt") ?? "";
  const offset = Number(params.get("offset") ?? 0);
  const [actionError, setActionError] = useState<string>();

  const datasets = useAsync(() => api.listDatasets(), []);
  const runs = useAsync(
    () =>
      api.listRuns({
        dataset_id: datasetId ? Number(datasetId) : undefined,
        model_name: model || undefined,
        prompt_version: prompt || undefined,
        limit: PAGE,
        offset,
      }),
    [datasetId, model, prompt, offset],
  );

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("offset");
    setParams(next);
  }

  function page(delta: number) {
    const next = new URLSearchParams(params);
    next.set("offset", String(Math.max(0, offset + delta)));
    setParams(next);
  }

  async function remove(id: number) {
    if (!window.confirm(`Delete run #${id} and its results?`)) return;
    try {
      await api.deleteRun(id);
      runs.reload();
    } catch (err) {
      setActionError((err as Error).message);
    }
  }

  return (
    <>
      <PageHeader title="Run history" subtitle="Every evaluation run, newest first." actions={<Link className="btn btn-primary" to="/runs/new">New evaluation</Link>} />
      {actionError && <ErrorBanner error={actionError} />}
      <Card>
        <div className="filters">
          <label>
            Dataset
            <select value={datasetId} onChange={(e) => setFilter("dataset", e.target.value)}>
              <option value="">All</option>
              {datasets.data?.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Model
            <input defaultValue={model} onBlur={(e) => setFilter("model", e.target.value.trim())} placeholder="exact name" />
          </label>
          <label>
            Prompt version
            <input defaultValue={prompt} onBlur={(e) => setFilter("prompt", e.target.value.trim())} placeholder="e.g. v1.4" />
          </label>
        </div>
        {runs.loading && !runs.data ? (
          <Loading />
        ) : runs.error ? (
          <ErrorBanner error={runs.error} onRetry={runs.reload} />
        ) : !runs.data?.items.length ? (
          <Empty>No runs match.</Empty>
        ) : (
          <>
            <table className="table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Dataset</th>
                  <th>Model / prompt</th>
                  <th>Passed</th>
                  <th>Pass rate</th>
                  <th>Mean F1</th>
                  <th>p95</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {runs.data.items.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link to={`/runs/${r.id}`}>#{r.id}</Link>
                    </td>
                    <td>{r.dataset_name}</td>
                    <td>
                      <div>{r.model_name}</div>
                      <div className="muted small">
                        {r.prompt_version} {r.label && `· ${r.label}`}
                      </div>
                    </td>
                    <td>
                      {r.summary.passed_cases}/{r.summary.total_cases}
                    </td>
                    <td className="w-meter">
                      <Meter value={r.pass_rate} label={`pass rate run ${r.id}`} />
                    </td>
                    <td>{num(r.summary.mean_token_f1)}</td>
                    <td>{ms(r.summary.latency_p95_ms)}</td>
                    <td className="muted small">{dateTime(r.created_at)}</td>
                    <td className="row-actions">
                      <Link className="btn btn-small" to={`/reports?run=${r.id}`}>
                        Report
                      </Link>
                      <button className="btn btn-small btn-danger" onClick={() => remove(r.id)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="pager">
              <span className="muted small">
                {offset + 1}–{offset + runs.data.items.length} of {runs.data.total}
              </span>
              <button className="btn btn-small" disabled={offset === 0} onClick={() => page(-PAGE)}>
                Previous
              </button>
              <button className="btn btn-small" disabled={offset + PAGE >= runs.data.total} onClick={() => page(PAGE)}>
                Next
              </button>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
