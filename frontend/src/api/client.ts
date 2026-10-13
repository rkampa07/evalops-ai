import type {
  Comparison,
  Dataset,
  GateConfig,
  Passage,
  GateEvaluation,
  Report,
  RunCreate,
  RunDetail,
  RunList,
  TestCase,
  TestCaseInput,
} from "./types";

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return `${API_BASE_URL}${path}${qs ? `?${qs}` : ""}`;
}

async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  let res: Response;
  try {
    res = await fetch(buildUrl(path, query), {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "network_error", `Cannot reach the EvalOps API at ${API_BASE_URL}. Is the backend running?`);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    // non-JSON body (e.g. proxy error page); fall through to a generic message
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } } | undefined)?.error;
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? `Request failed (HTTP ${res.status})`);
  }
  return data as T;
}

export const api = {
  health: () => request<{ status: string; version: string; database: string }>("GET", "/health"),

  listDatasets: () => request<Dataset[]>("GET", "/datasets"),
  getDataset: (id: number) => request<Dataset>("GET", `/datasets/${id}`),
  createDataset: (body: { name: string; description?: string }) => request<Dataset>("POST", "/datasets", body),
  updateDataset: (id: number, body: { name?: string; description?: string }) =>
    request<Dataset>("PATCH", `/datasets/${id}`, body),
  deleteDataset: (id: number) => request<void>("DELETE", `/datasets/${id}`),

  listPassages: (datasetId: number) => request<Passage[]>("GET", `/datasets/${datasetId}/passages`),
  listTestCases: (datasetId: number) => request<TestCase[]>("GET", `/datasets/${datasetId}/test-cases`),
  createTestCase: (datasetId: number, body: TestCaseInput) =>
    request<TestCase>("POST", `/datasets/${datasetId}/test-cases`, body),
  bulkCreateTestCases: (datasetId: number, cases: TestCaseInput[]) =>
    request<TestCase[]>("POST", `/datasets/${datasetId}/test-cases/bulk`, { test_cases: cases }),
  updateTestCase: (id: number, body: Partial<TestCaseInput>) => request<TestCase>("PATCH", `/test-cases/${id}`, body),
  deleteTestCase: (id: number) => request<void>("DELETE", `/test-cases/${id}`),

  listRuns: (query?: { dataset_id?: number; model_name?: string; prompt_version?: string; limit?: number; offset?: number }) =>
    request<RunList>("GET", "/evaluations/runs", undefined, query),
  getRun: (id: number) => request<RunDetail>("GET", `/evaluations/runs/${id}`),
  createRun: (body: RunCreate) => request<RunDetail>("POST", "/evaluations/runs", body),
  deleteRun: (id: number) => request<void>("DELETE", `/evaluations/runs/${id}`),

  compare: (baselineRunId: number, candidateRunId: number) =>
    request<Comparison>("GET", "/evaluations/compare", undefined, {
      baseline_run_id: baselineRunId,
      candidate_run_id: candidateRunId,
    }),
  evaluateGates: (body: { candidate_run_id: number; baseline_run_id?: number | null; gates: GateConfig }) =>
    request<GateEvaluation>("POST", "/evaluations/gates", body),
  report: (runId: number, baselineRunId?: number | null) =>
    request<Report>("GET", `/evaluations/runs/${runId}/report`, undefined, { baseline_run_id: baselineRunId }),
};
