import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Dataset, TestCaseInput } from "../api/types";
import { Card, Empty, ErrorBanner, Loading, PageHeader, PaginationControls } from "../components/ui";
import { dateTime } from "../lib/format";
import { useAsync } from "../lib/useAsync";

const PAGE_SIZE = 50;

export function Datasets() {
  const [offset, setOffset] = useState(0);
  const { data, error, loading, reload } = useAsync(
    () => api.listDatasetsPage({ limit: PAGE_SIZE, offset }),
    [offset],
  );
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [importText, setImportText] = useState("");
  const [formError, setFormError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [createdDataset, setCreatedDataset] = useState<Dataset>();

  async function create(e: FormEvent) {
    e.preventDefault();
    setFormError(undefined);
    setBusy(true);
    try {
      const created = await api.createDataset({ name, description });
      setCreatedDataset(created);
      setName("");
      setDescription("");
      reload();
    } catch (err) {
      setFormError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function importJson(e: FormEvent) {
    e.preventDefault();
    setFormError(undefined);
    let parsed: { name?: string; description?: string; test_cases?: TestCaseInput[] };
    try {
      parsed = JSON.parse(importText);
    } catch {
      setFormError("Import: the text is not valid JSON.");
      return;
    }
    if (!parsed.name || !Array.isArray(parsed.test_cases)) {
      setFormError('Import: expected {"name": ..., "description": ..., "test_cases": [...]}.');
      return;
    }
    setBusy(true);
    try {
      const ds = await api.createDataset({ name: parsed.name, description: parsed.description ?? "" });
      try {
        await api.bulkCreateTestCases(ds.id, parsed.test_cases);
      } catch (err) {
        await api.deleteDataset(ds.id); // keep import all-or-nothing
        throw err;
      }
      setImportText("");
      setCreatedDataset(ds);
      reload();
    } catch (err) {
      setFormError(`Import failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function readFile(file: File | undefined) {
    if (file) setImportText(await file.text());
  }

  return (
    <>
      <PageHeader title="Datasets" subtitle="Golden test sets. Each test case defines the checks a response must pass." />
      {formError && <ErrorBanner error={formError} />}
      {createdDataset && (
        <p role="status" className="muted">
          Created <Link to={`/datasets/${createdDataset.id}`}>{createdDataset.name}</Link>.
        </p>
      )}
      <div className="grid-2">
        <Card title="New dataset">
          <form onSubmit={create} className="form">
            <label>
              Name
              <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={200} />
            </label>
            <label>
              Description
              <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} maxLength={2000} />
            </label>
            <button className="btn btn-primary" disabled={busy || !name.trim()}>
              Create dataset
            </button>
          </form>
        </Card>
        <Card title="Import dataset JSON">
          <form onSubmit={importJson} className="form">
            <input type="file" accept="application/json,.json" aria-label="Dataset JSON file" onChange={(e) => readFile(e.target.files?.[0])} />
            <textarea
              aria-label="Dataset JSON"
              value={importText}
              onChange={(e) => setImportText(e.target.value)}
              rows={4}
              placeholder='{"name": "...", "test_cases": [{"key": "...", "input": "...", "expected_output": "..."}]}'
              className="mono"
            />
            <button className="btn" disabled={busy || !importText.trim()}>
              Import
            </button>
          </form>
        </Card>
      </div>

      <Card title={`All datasets${data ? ` (${data.total})` : ""}`}>
        {loading ? (
          <Loading />
        ) : error ? (
          <ErrorBanner error={error} onRetry={reload} />
        ) : !data?.items.length ? (
          <Empty>No datasets yet.</Empty>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Description</th>
                <th>Test cases</th>
                <th>Runs</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((d) => (
                <tr key={d.id}>
                  <td>
                    <Link to={`/datasets/${d.id}`}>{d.name}</Link>
                  </td>
                  <td className="muted">{d.description}</td>
                  <td>{d.test_case_count}</td>
                  <td>{d.run_count}</td>
                  <td className="muted small">{dateTime(d.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data && (
          <PaginationControls
            limit={data.limit}
            offset={offset}
            total={data.total}
            loading={loading}
            onOffsetChange={setOffset}
          />
        )}
      </Card>
    </>
  );
}
