import { Link } from "react-router-dom";
import { api } from "../api/client";
import { PassRateChart } from "../components/PassRateChart";
import { Card, Empty, ErrorBanner, Loading, Meter, PageHeader, Stat } from "../components/ui";
import { dateTime, ms, num, pct } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export function Overview() {
  const { data, error, loading, reload } = useAsync(
    () => Promise.all([api.listDatasets(), api.listRuns({ limit: 20 })]),
    [],
  );

  if (loading && !data) return <Loading what="Loading overview" />;
  if (error) return <ErrorBanner error={error} onRetry={reload} />;
  if (!data) return null;

  const [datasets, runList] = data;
  const runs = runList.items;
  const latest = runs[0];
  const totalCases = datasets.reduce((n, d) => n + d.test_case_count, 0);

  return (
    <>
      <PageHeader title="Overview" subtitle="Evaluation activity across all datasets, from the EvalOps API." />

      <div className="stat-grid">
        <Stat label="Datasets" value={datasets.length} hint={`${totalCases} test cases`} />
        <Stat label="Evaluation runs" value={runList.total} />
        <Stat
          label="Latest pass rate"
          value={latest ? pct(latest.pass_rate) : "n/a"}
          hint={latest ? `run #${latest.id}` : "no runs yet"}
          tone={latest?.pass_rate == null ? undefined : latest.pass_rate >= 0.9 ? "good" : "bad"}
        />
        <Stat
          label="Latest p95 latency"
          value={latest ? ms(latest.summary.latency_p95_ms) : "n/a"}
          hint={latest ? `mean F1 ${num(latest.summary.mean_token_f1)}` : undefined}
        />
      </div>

      {runs.length === 0 ? (
        <Card>
          <Empty>
            No evaluation runs yet. <Link to="/datasets">Create a dataset</Link> and then{" "}
            <Link to="/runs/new">submit responses</Link>, or run <code>make seed</code> to load the demo.
          </Empty>
        </Card>
      ) : (
        <>
          <Card title="Pass rate by run (last 20)">
            <PassRateChart runs={runs} />
          </Card>
          <Card title="Recent runs" actions={<Link to="/runs">All runs →</Link>}>
            <table className="table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Dataset</th>
                  <th>Model / prompt</th>
                  <th>Pass rate</th>
                  <th>Forbidden</th>
                  <th>p95</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {runs.slice(0, 8).map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link to={`/runs/${r.id}`}>#{r.id}</Link>
                    </td>
                    <td>{r.dataset_name}</td>
                    <td>
                      <div>{r.model_name}</div>
                      <div className="muted small">{r.prompt_version} {r.label && `· ${r.label}`}</div>
                    </td>
                    <td className="w-meter"><Meter value={r.pass_rate} label={`pass rate run ${r.id}`} /></td>
                    <td>{r.summary.forbidden_violation_count}</td>
                    <td>{ms(r.summary.latency_p95_ms)}</td>
                    <td className="muted small">{dateTime(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      )}
    </>
  );
}
