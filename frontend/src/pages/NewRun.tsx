import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { ResponseInput } from "../api/types";
import { Card, ErrorBanner, Loading, PageHeader } from "../components/ui";
import { useAsync } from "../lib/useAsync";

interface ResponseFile {
  model_name?: string;
  prompt_version?: string;
  label?: string;
  config?: { min_token_f1?: number; default_max_latency_ms?: number | null };
  responses?: ResponseInput[];
}

/** Accepts either a bare array of responses or an object with a "responses" array. */
export function parseResponses(text: string): ResponseFile {
  const parsed: unknown = JSON.parse(text);
  const file: ResponseFile = Array.isArray(parsed) ? { responses: parsed as ResponseInput[] } : (parsed as ResponseFile);
  if (!file || !Array.isArray(file.responses) || file.responses.length === 0) {
    throw new Error('Expected a JSON array of responses or an object with a non-empty "responses" array.');
  }
  for (const [i, r] of file.responses.entries()) {
    if (typeof r?.case_key !== "string" || typeof r?.output !== "string") {
      throw new Error(`Response ${i + 1} needs string "case_key" and "output" fields.`);
    }
  }
  return file;
}

export function NewRun() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const datasets = useAsync(() => api.listDatasets(), []);
  const [datasetId, setDatasetId] = useState(params.get("dataset") ?? "");
  const [modelName, setModelName] = useState("");
  const [promptVersion, setPromptVersion] = useState("");
  const [label, setLabel] = useState("");
  const [minF1, setMinF1] = useState("0.5");
  const [maxLatency, setMaxLatency] = useState("");
  const [requireExact, setRequireExact] = useState(false);
  const [responsesText, setResponsesText] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  function loadText(text: string) {
    setResponsesText(text);
    try {
      const file = parseResponses(text);
      if (file.model_name) setModelName(file.model_name);
      if (file.prompt_version) setPromptVersion(file.prompt_version);
      if (file.label) setLabel(file.label);
      if (file.config?.min_token_f1 !== undefined) setMinF1(String(file.config.min_token_f1));
      if (file.config?.default_max_latency_ms) setMaxLatency(String(file.config.default_max_latency_ms));
      setError(undefined);
    } catch {
      /* validated on submit */
    }
  }

  async function downloadTemplate() {
    if (!datasetId) return;
    const cases = await api.listTestCases(Number(datasetId));
    const template = {
      model_name: modelName || "your-model",
      prompt_version: promptVersion || "v1",
      responses: cases.map((c) => ({ case_key: c.key, input: c.input, output: "", latency_ms: null })),
    };
    const blob = new Blob([JSON.stringify(template, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `responses-template-dataset-${datasetId}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(undefined);
    let file: ResponseFile;
    try {
      file = parseResponses(responsesText);
    } catch (err) {
      setError(err instanceof SyntaxError ? "Responses are not valid JSON." : (err as Error).message);
      return;
    }
    setBusy(true);
    try {
      const run = await api.createRun({
        dataset_id: Number(datasetId),
        model_name: modelName,
        prompt_version: promptVersion,
        label,
        config: {
          min_token_f1: Number(minF1),
          require_exact_match: requireExact,
          default_max_latency_ms: maxLatency ? Number(maxLatency) : null,
        },
        // strip helper fields (e.g. "input" from the template) before sending
        responses: file.responses!.map(({ case_key, output, latency_ms }) => ({ case_key, output, latency_ms: latency_ms ?? null })),
      });
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (datasets.loading && !datasets.data) return <Loading />;
  if (datasets.error) return <ErrorBanner error={datasets.error} onRetry={datasets.reload} />;

  return (
    <>
      <PageHeader
        title="New evaluation run"
        subtitle="Submit responses your AI application produced for a dataset. EvalOps scores them; it does not call any model."
      />
      {error && <ErrorBanner error={error} />}
      <form className="form" onSubmit={submit}>
        <Card title="Run details">
          <div className="grid-2">
            <label>
              Dataset
              <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)} required>
                <option value="">Select a dataset…</option>
                {datasets.data?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} ({d.test_case_count} cases)
                  </option>
                ))}
              </select>
            </label>
            <label>
              Label
              <input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={200} placeholder="e.g. Candidate: new prompt" />
            </label>
            <label>
              Model name
              <input value={modelName} onChange={(e) => setModelName(e.target.value)} required maxLength={200} />
            </label>
            <label>
              Prompt version
              <input value={promptVersion} onChange={(e) => setPromptVersion(e.target.value)} required maxLength={100} />
            </label>
          </div>
        </Card>
        <Card title="Scoring policy">
          <div className="grid-3">
            <label>
              Min token F1 per case
              <input type="number" min={0} max={1} step={0.05} value={minF1} onChange={(e) => setMinF1(e.target.value)} required />
            </label>
            <label>
              Default max latency (ms)
              <input type="number" min={1} value={maxLatency} onChange={(e) => setMaxLatency(e.target.value)} placeholder="none" />
            </label>
            <label className="checkbox">
              <input type="checkbox" checked={requireExact} onChange={(e) => setRequireExact(e.target.checked)} />
              Require exact match
            </label>
          </div>
        </Card>
        <Card
          title="Responses"
          actions={
            <button type="button" className="btn btn-small" disabled={!datasetId} onClick={downloadTemplate}>
              Download template
            </button>
          }
        >
          <input type="file" accept="application/json,.json" aria-label="Responses JSON file" onChange={async (e) => {
            const f = e.target.files?.[0];
            if (f) loadText(await f.text());
          }} />
          <textarea
            aria-label="Responses JSON"
            className="mono"
            rows={10}
            value={responsesText}
            onChange={(e) => loadText(e.target.value)}
            placeholder='[{"case_key": "policy-return-window", "output": "...", "latency_ms": 820}]'
            required
          />
          <p className="muted small">
            Cases without a response count as failures. Unknown case keys are rejected.
          </p>
        </Card>
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "Evaluating…" : "Run evaluation"}
        </button>
      </form>
    </>
  );
}
