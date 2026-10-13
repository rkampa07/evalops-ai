import { Fragment, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { TestCaseForm } from "../components/TestCaseForm";
import { Badge, Card, Empty, ErrorBanner, Loading, PageHeader } from "../components/ui";
import { useAsync } from "../lib/useAsync";

export function DatasetDetail() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const { data, error, loading, reload } = useAsync(
    () => Promise.all([api.getDataset(id), api.listTestCases(id), api.listPassages(id)]),
    [id],
  );
  const [showPassages, setShowPassages] = useState(false);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<number>();
  const [expanded, setExpanded] = useState<number>();
  const [actionError, setActionError] = useState<string>();

  if (loading && !data) return <Loading />;
  if (error) return <ErrorBanner error={error} onRetry={reload} />;
  if (!data) return null;
  const [dataset, cases, passages] = data;

  async function removeDataset() {
    if (!window.confirm(`Delete dataset "${dataset.name}" and all its test cases?`)) return;
    try {
      await api.deleteDataset(dataset.id);
      navigate("/datasets");
    } catch (err) {
      setActionError((err as Error).message);
    }
  }

  async function removeCase(caseId: number, key: string) {
    if (!window.confirm(`Delete test case "${key}"?`)) return;
    try {
      await api.deleteTestCase(caseId);
      reload();
    } catch (err) {
      setActionError((err as Error).message);
    }
  }

  return (
    <>
      <PageHeader
        title={dataset.name}
        subtitle={
          <>
            {dataset.description || "No description"} <Badge tone="neutral">{`version ${dataset.version}`}</Badge>
          </>
        }
        actions={
          <>
            <Link className="btn" to={`/runs/new?dataset=${dataset.id}`}>
              Evaluate responses
            </Link>
            <Link className="btn" to={`/runs?dataset=${dataset.id}`}>
              Runs ({dataset.run_count})
            </Link>
            <button className="btn btn-danger" onClick={removeDataset}>
              Delete
            </button>
          </>
        }
      />
      {actionError && <ErrorBanner error={actionError} />}

      {passages.length > 0 && (
        <Card
          title={`Knowledge base (${passages.length} passages)`}
          actions={
            <button className="btn btn-small" onClick={() => setShowPassages(!showPassages)} aria-expanded={showPassages}>
              {showPassages ? "Hide" : "Show"}
            </button>
          }
        >
          {showPassages ? (
            <table className="table compact">
              <tbody>
                {passages.map((p) => (
                  <tr key={p.passage_id}>
                    <td className="mono">{p.passage_id}</td>
                    <td>
                      <strong>{p.title}</strong> {p.text}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted small">Passages answers are graded against. Their ids are the citation markers, e.g. [{passages[0]?.passage_id}].</p>
          )}
        </Card>
      )}

      <Card
        title={`Test cases (${cases.length})`}
        actions={
          !adding && (
            <button className="btn btn-primary btn-small" onClick={() => setAdding(true)}>
              Add test case
            </button>
          )
        }
      >
        {adding && (
          <div className="inset">
            <TestCaseForm
              submitLabel="Create test case"
              onCancel={() => setAdding(false)}
              onSubmit={async (value) => {
                await api.createTestCase(dataset.id, value);
                setAdding(false);
                reload();
              }}
            />
          </div>
        )}
        {cases.length === 0 ? (
          <Empty>No test cases yet. Add one above or import a dataset JSON.</Empty>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Key</th>
                <th>Input</th>
                <th>Checks</th>
                <th>Tags</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => (
                <Fragment key={c.id}>
                  <tr>
                    <td className="mono">
                      <button className="link" onClick={() => setExpanded(expanded === c.id ? undefined : c.id)} aria-expanded={expanded === c.id}>
                        {c.key}
                      </button>{" "}
                      <span className="muted small" title={`definition hash ${c.content_hash}`}>
                        v{c.version}
                      </span>
                    </td>
                    <td className="truncate">{c.input}</td>
                    <td>
                      <div className="badges">
                        {c.expected_output !== null && <Badge tone="info">reference</Badge>}
                        {c.required_terms.length > 0 && <Badge tone="info">required ×{c.required_terms.length}</Badge>}
                        {c.forbidden_terms.length > 0 && <Badge tone="warn">forbidden ×{c.forbidden_terms.length}</Badge>}
                        {c.json_schema && <Badge tone="info">schema</Badge>}
                        {c.max_latency_ms !== null && <Badge tone="neutral">≤{c.max_latency_ms} ms</Badge>}
                        {c.relevant_passage_ids?.length > 0 && <Badge tone="info">{`gold ${c.relevant_passage_ids.join(", ")}`}</Badge>}
                        {c.expect_abstain && <Badge tone="warn">should decline</Badge>}
                      </div>
                    </td>
                    <td className="muted small">{c.tags.join(", ")}</td>
                    <td className="row-actions">
                      <button className="btn btn-small" onClick={() => setEditing(c.id)}>
                        Edit
                      </button>
                      <button className="btn btn-small btn-danger" onClick={() => removeCase(c.id, c.key)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                  {editing === c.id && (
                    <tr>
                      <td colSpan={5}>
                        <TestCaseForm
                          initial={c}
                          submitLabel="Save changes"
                          onCancel={() => setEditing(undefined)}
                          onSubmit={async (value) => {
                            await api.updateTestCase(c.id, value);
                            setEditing(undefined);
                            reload();
                          }}
                        />
                      </td>
                    </tr>
                  )}
                  {expanded === c.id && editing !== c.id && (
                    <tr className="detail-row">
                      <td colSpan={5}>
                        <dl className="kv">
                          <dt>Input</dt>
                          <dd>{c.input}</dd>
                          <dt>Expected</dt>
                          <dd>{c.expected_output ?? <span className="muted">none</span>}</dd>
                          <dt>Required</dt>
                          <dd>{c.required_terms.join(", ") || <span className="muted">none</span>}</dd>
                          <dt>Forbidden</dt>
                          <dd>{c.forbidden_terms.join(", ") || <span className="muted">none</span>}</dd>
                          {c.json_schema && (
                            <>
                              <dt>Schema</dt>
                              <dd>
                                <pre>{JSON.stringify(c.json_schema, null, 2)}</pre>
                              </dd>
                            </>
                          )}
                        </dl>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}
