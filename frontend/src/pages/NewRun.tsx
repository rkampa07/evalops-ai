import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { EvaluationJob, EvaluationJobStatus, ResponseInput, RunCreate } from "../api/types";
import { Badge, Card, ErrorBanner, Loading, PageHeader } from "../components/ui";
import { useAsync } from "../lib/useAsync";

interface ResponseFile {
  model_name?: string;
  prompt_version?: string;
  label?: string;
  config?: { min_token_f1?: number; default_max_latency_ms?: number | null };
  responses?: ResponseInput[];
}

interface Draft {
  datasetId: string;
  modelName: string;
  promptVersion: string;
  label: string;
  minF1: string;
  maxLatency: string;
  requireExact: boolean;
  responsesText: string;
}

interface SavedSubmission {
  draft: Draft;
  idempotencyKey?: string;
  requestFingerprint?: string;
  jobId?: number;
  submittedAt?: number;
}

const STORAGE_KEY = "evalops.new-run.v1";
const FIRST_POLL_MS = 1000;
const MAX_POLL_MS = 5000;
const WORKER_WAIT_MS = 30_000;

function emptyDraft(datasetId = ""): Draft {
  return {
    datasetId,
    modelName: "",
    promptVersion: "",
    label: "",
    minF1: "0.5",
    maxLatency: "",
    requireExact: false,
    responsesText: "",
  };
}

function readSavedSubmission(datasetId: string): SavedSubmission {
  try {
    const value = sessionStorage.getItem(STORAGE_KEY);
    if (value) {
      const parsed = JSON.parse(value) as SavedSubmission;
      if (parsed.draft && typeof parsed.draft.responsesText === "string") return parsed;
    }
  } catch {
    // Storage may be disabled; the form still works for this visit.
  }
  return { draft: emptyDraft(datasetId) };
}

function saveSubmission(value: SavedSubmission) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // Keep the in-memory submission flow if browser storage is unavailable.
  }
}

function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Accepts a bare array or an object containing a responses array. */
export function parseResponses(text: string): ResponseFile {
  const parsed: unknown = JSON.parse(text);
  const file: ResponseFile = Array.isArray(parsed) ? { responses: parsed as ResponseInput[] } : (parsed as ResponseFile);
  if (!file || !Array.isArray(file.responses) || file.responses.length === 0) {
    throw new Error('Expected a JSON array of responses or an object with a non-empty "responses" array.');
  }
  for (const [i, response] of file.responses.entries()) {
    if (typeof response?.case_key !== "string" || typeof response.output !== "string") {
      throw new Error(`Response ${i + 1} needs string "case_key" and "output" fields.`);
    }
  }
  return file;
}

function makeRequest(draft: Draft, file: ResponseFile): RunCreate {
  return {
    dataset_id: Number(draft.datasetId),
    model_name: draft.modelName,
    prompt_version: draft.promptVersion,
    label: draft.label,
    config: {
      min_token_f1: Number(draft.minF1),
      require_exact_match: draft.requireExact,
      default_max_latency_ms: draft.maxLatency ? Number(draft.maxLatency) : null,
    },
    // Strip template-only fields before sending the evaluation request.
    responses: file.responses!.map(({ case_key, output, latency_ms }) => ({
      case_key,
      output,
      latency_ms: latency_ms ?? null,
    })),
  };
}

function terminal(status: EvaluationJobStatus): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}

function statusLabel(status: EvaluationJobStatus): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function statusTone(status: EvaluationJobStatus): "good" | "bad" | "warn" | "info" | "neutral" {
  if (status === "succeeded") return "good";
  if (status === "failed") return "bad";
  if (status === "cancelled") return "neutral";
  if (status === "running") return "info";
  return "warn";
}

export function NewRun() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const datasets = useAsync(() => api.listDatasets(), []);
  const [saved, setSaved] = useState(() => readSavedSubmission(params.get("dataset") ?? ""));
  const [draft, setDraft] = useState<Draft>(saved.draft);
  const [activeJobId, setActiveJobId] = useState<number | null>(saved.jobId ?? null);
  const [job, setJob] = useState<EvaluationJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [pollError, setPollError] = useState<string>();
  const [pollRevision, setPollRevision] = useState(0);
  const [waitingForWorker, setWaitingForWorker] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const busyRef = useRef(false);
  const pollSequenceRef = useRef(0);
  const activeJobIdRef = useRef(activeJobId);
  activeJobIdRef.current = activeJobId;

  useEffect(() => {
    saveSubmission({ ...saved, draft });
  }, [draft, saved]);

  useEffect(() => {
    if (activeJobId === null) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let delay = FIRST_POLL_MS;
    const controller = new AbortController();
    const submittedAt = saved.submittedAt ?? Date.now();

    const poll = async () => {
      const sequence = ++pollSequenceRef.current;
      try {
        const latest = await api.getEvaluationJob(activeJobId, controller.signal);
        if (stopped || activeJobIdRef.current !== activeJobId || sequence !== pollSequenceRef.current) return;
        setJob(latest);
        setPollError(undefined);
        setWaitingForWorker(
          latest.status === "queued" && Date.now() - submittedAt >= WORKER_WAIT_MS,
        );
        if (latest.status === "succeeded" && latest.run_id !== null) {
          try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* storage is optional */ }
          navigate(`/runs/${latest.run_id}`);
          return;
        }
        if (terminal(latest.status)) {
          saveSubmission({ ...saved, jobId: activeJobId, draft });
          return;
        }
      } catch (cause) {
        if (stopped || controller.signal.aborted || sequence !== pollSequenceRef.current) return;
        setPollError(cause instanceof Error ? cause.message : "Job status is temporarily unavailable.");
        setWaitingForWorker(Date.now() - submittedAt >= WORKER_WAIT_MS);
      }
      if (stopped || controller.signal.aborted) return;
      timer = setTimeout(poll, delay);
      delay = Math.min(MAX_POLL_MS, Math.round(delay * 1.5));
    };

    void poll();
    return () => {
      stopped = true;
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [activeJobId, draft, navigate, pollRevision, saved]);

  function updateDraft<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
    setError(undefined);
  }

  function loadText(text: string) {
    setDraft((current) => ({ ...current, responsesText: text }));
    try {
      const file = parseResponses(text);
      setDraft((current) => ({
        ...current,
        responsesText: text,
        modelName: file.model_name || current.modelName,
        promptVersion: file.prompt_version || current.promptVersion,
        label: file.label || current.label,
        minF1: file.config?.min_token_f1 === undefined ? current.minF1 : String(file.config.min_token_f1),
        maxLatency: file.config?.default_max_latency_ms ? String(file.config.default_max_latency_ms) : current.maxLatency,
      }));
      setError(undefined);
    } catch {
      // Validated on submit so users can finish editing a JSON document.
    }
  }

  async function downloadTemplate() {
    if (!draft.datasetId) return;
    const cases = await api.listTestCases(Number(draft.datasetId));
    const template = {
      model_name: draft.modelName || "your-model",
      prompt_version: draft.promptVersion || "v1",
      responses: cases.map((testCase) => ({ case_key: testCase.key, input: testCase.input, output: "", latency_ms: null })),
    };
    const blob = new Blob([JSON.stringify(template, null, 2)], { type: "application/json" });
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(blob);
    anchor.download = `responses-template-dataset-${draft.datasetId}.json`;
    anchor.click();
    URL.revokeObjectURL(anchor.href);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busyRef.current || (job && !terminal(job.status))) return;
    setError(undefined);
    let file: ResponseFile;
    try {
      file = parseResponses(draft.responsesText);
    } catch (cause) {
      setError(cause instanceof SyntaxError ? "Responses are not valid JSON." : (cause as Error).message);
      return;
    }
    const body = makeRequest(draft, file);
    const fingerprint = JSON.stringify(body);
    const reuseKey = saved.idempotencyKey && saved.requestFingerprint === fingerprint && !saved.jobId;
    const key = reuseKey ? saved.idempotencyKey! : newIdempotencyKey();
    const pending: SavedSubmission = {
      draft,
      idempotencyKey: key,
      requestFingerprint: fingerprint,
      submittedAt: Date.now(),
    };
    saveSubmission(pending);
    setSaved(pending);
    busyRef.current = true;
    setBusy(true);
    try {
      const submitted = await api.createEvaluationJob(body, key);
      const accepted = { ...pending, jobId: submitted.job_id };
      saveSubmission(accepted);
      setSaved(accepted);
      setJob(submitted);
      setActiveJobId(submitted.job_id);
      setWaitingForWorker(false);
    } catch (cause) {
      setError(
        `${cause instanceof Error ? cause.message : "The submission outcome is unknown."} ` +
        "Retry this unchanged request to safely reuse its submission key.",
      );
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function cancelJob() {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const confirmed = window.confirm(
      "Cancel this evaluation? If it is running, cancellation may take effect after the current case.",
    );
    if (!confirmed) return;
    pollSequenceRef.current += 1;
    setCancelling(true);
    setPollError(undefined);
    try {
      const updated = await api.cancelEvaluationJob(job.job_id);
      setJob(updated);
    } catch (cause) {
      setPollError(cause instanceof Error ? cause.message : "Cancellation could not be confirmed.");
    } finally {
      setCancelling(false);
      setPollRevision((revision) => revision + 1);
    }
  }

  if (datasets.loading && !datasets.data) return <Loading />;
  if (datasets.error) return <ErrorBanner error={datasets.error} onRetry={datasets.reload} />;

  const progress = job?.progress;
  const progressPercent = progress?.total ? Math.round((progress.completed / progress.total) * 100) : 0;
  const formDisabled = busy || Boolean(job && !terminal(job.status));

  return (
    <>
      <PageHeader
        title="New evaluation run"
        subtitle="Submit recorded responses for deterministic scoring. EvalOps does not call any model."
      />
      {error && <ErrorBanner error={error} />}
      {(activeJobId !== null || (job && terminal(job.status))) && (
        <Card title={`Evaluation job #${job?.job_id ?? activeJobId}`} className="job-status-card">
          {!job ? (
            <p role="status">Checking saved job status…</p>
          ) : (
            <>
              <p><Badge tone={statusTone(job.status)}>{statusLabel(job.status)}</Badge></p>
              <p aria-live="polite">{job.progress.completed} of {job.progress.total} cases completed</p>
              <progress aria-label="Evaluation progress" max={100} value={progressPercent} />
              {job.status === "queued" && waitingForWorker && (
                <p className="muted" role="status">
                  This job is still queued. The worker may be stopped or busy; the job remains saved and will continue when a worker is available.
                </p>
              )}
              {job.cancellation_requested_at && job.status === "running" && (
                <p className="muted" role="status">Cancellation requested. It may take effect after the current case.</p>
              )}
              {job.status === "failed" && job.error && <ErrorBanner error={job.error.message} />}
              {job.status === "cancelled" && <p role="status">This evaluation was cancelled.</p>}
              {pollError && <ErrorBanner error={`${pollError} Status polling will retry automatically.`} />}
              {!["failed", "cancelled", "succeeded"].includes(job.status) && (
                <button type="button" className="btn btn-small" disabled={cancelling} onClick={cancelJob}>
                  {cancelling ? "Requesting cancellation…" : "Cancel evaluation"}
                </button>
              )}
              {terminal(job.status) && job.status !== "succeeded" && (
                <p className="muted small">You can start a new attempt below. A new attempt receives a new submission key.</p>
              )}
            </>
          )}
        </Card>
      )}
      <form className="form" onSubmit={submit}>
        <Card title="Run details">
          <div className="grid-2">
            <label>
              Dataset
              <select value={draft.datasetId} onChange={(event) => updateDraft("datasetId", event.target.value)} required disabled={formDisabled}>
                <option value="">Select a dataset…</option>
                {datasets.data?.map((item) => (
                  <option key={item.id} value={item.id}>{item.name} ({item.test_case_count} cases)</option>
                ))}
              </select>
            </label>
            <label>
              Label
              <input value={draft.label} onChange={(event) => updateDraft("label", event.target.value)} maxLength={200} placeholder="e.g. Candidate: new prompt" disabled={formDisabled} />
            </label>
            <label>
              Model name
              <input value={draft.modelName} onChange={(event) => updateDraft("modelName", event.target.value)} required maxLength={200} disabled={formDisabled} />
            </label>
            <label>
              Prompt version
              <input value={draft.promptVersion} onChange={(event) => updateDraft("promptVersion", event.target.value)} required maxLength={100} disabled={formDisabled} />
            </label>
          </div>
        </Card>
        <Card title="Scoring policy">
          <div className="grid-3">
            <label>
              Min token F1 per case
              <input type="number" min={0} max={1} step={0.05} value={draft.minF1} onChange={(event) => updateDraft("minF1", event.target.value)} required disabled={formDisabled} />
            </label>
            <label>
              Default max latency (ms)
              <input type="number" min={1} value={draft.maxLatency} onChange={(event) => updateDraft("maxLatency", event.target.value)} placeholder="none" disabled={formDisabled} />
            </label>
            <label className="checkbox">
              <input type="checkbox" checked={draft.requireExact} onChange={(event) => updateDraft("requireExact", event.target.checked)} disabled={formDisabled} />
              Require exact match
            </label>
          </div>
        </Card>
        <Card
          title="Responses"
          actions={
            <button type="button" className="btn btn-small" disabled={!draft.datasetId || formDisabled} onClick={downloadTemplate}>
              Download template
            </button>
          }
        >
          <input type="file" accept="application/json,.json" aria-label="Responses JSON file" disabled={formDisabled} onChange={async (event) => {
            const file = event.target.files?.[0];
            if (file) loadText(await file.text());
          }} />
          <textarea
            aria-label="Responses JSON"
            className="mono"
            rows={10}
            value={draft.responsesText}
            onChange={(event) => loadText(event.target.value)}
            placeholder='[{"case_key": "policy-return-window", "output": "...", "latency_ms": 820}]'
            required
            disabled={formDisabled}
          />
          <p className="muted small">Cases without a response count as failures. Unknown case keys are rejected.</p>
        </Card>
        <button className="btn btn-primary" disabled={formDisabled}>
          {busy ? "Submitting…" : job?.status === "failed" || job?.status === "cancelled" ? "Start a new attempt" : "Queue evaluation"}
        </button>
      </form>
    </>
  );
}
