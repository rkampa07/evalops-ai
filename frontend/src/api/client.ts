import type {
  AssessmentCreate,
  AssessmentJob,
  AssessmentResult,
  ChatSession,
  ChatSessionDetail,
  ChatStatus,
  ChatTurn,
  CitationAssessmentCreate,
  CitationAssessmentJob,
  ClaimAssessmentCreate,
  ClaimAssessmentJob,
  AuthSession,
  Comparison,
  SaveChatTestCaseInput,
  Dataset,
  EvaluationJob,
  GateConfig,
  Passage,
  Page,
  GateEvaluation,
  Report,
  RunCreate,
  RunDetail,
  Result,
  RunList,
  Run,
  WorkspaceSummary,
  TestCase,
  TestCaseInput,
} from "./types";

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);

export interface PageQuery extends Record<string, string | number | boolean | null | undefined> {
  limit: number;
  offset: number;
}

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
let csrfToken: string | null = null;
let activeWorkspaceId: number | null = null;

function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return `${API_BASE_URL}${path}${qs ? `?${qs}` : ""}`;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  query?: Query,
  options: { headers?: Record<string, string>; signal?: AbortSignal } = {},
): Promise<T> {
  let res: Response;
  const headers = {
    ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    ...(method !== "GET" && method !== "HEAD" && csrfToken
      ? { "X-CSRF-Token": csrfToken }
      : {}),
    ...(activeWorkspaceId !== null ? { "X-Workspace-ID": String(activeWorkspaceId) } : {}),
    ...options.headers,
  };
  try {
    res = await fetch(buildUrl(path, query), {
      method,
      credentials: "include",
      headers: Object.keys(headers).length ? headers : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: options.signal,
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

  authSession: async () => {
    const session = await request<AuthSession>("GET", "/auth/session");
    csrfToken = session.csrf_token ?? null;
    activeWorkspaceId = session.active_workspace_id ?? null;
    return session;
  },
  logout: async () => {
    await request<void>("POST", "/auth/logout");
    csrfToken = null;
    activeWorkspaceId = null;
  },

  listWorkspaces: async () => request<{ items: WorkspaceSummary[]; active_workspace_id: number | null }>("GET", "/workspaces"),
  createWorkspace: (name: string) => request<WorkspaceSummary>("POST", "/workspaces", { name }),
  selectWorkspace: async (workspaceId: number) => {
    const session = await request<AuthSession>("PUT", "/auth/session/workspace", { workspace_id: workspaceId });
    activeWorkspaceId = session.active_workspace_id ?? workspaceId;
    // Current views keep workspace-specific data in component state/sessionStorage.
    if (typeof window !== "undefined") window.sessionStorage.clear();
    return session;
  },
  listWorkspaceMembers: (workspaceId: number) =>
    request<{ items: { user_id: number; display_name: string | null; email: string | null; role: string; joined_at: string }[] }>("GET", `/workspaces/${workspaceId}/members`),
  inviteWorkspaceMember: (workspaceId: number, targetUserId: number, role: "admin" | "editor" | "viewer") =>
    request<{ id: number; workspace_id: number; target_user_id: number; role: string; expires_at: string; token: string }>(
      "POST", `/workspaces/${workspaceId}/invitations`, { target_user_id: targetUserId, role },
    ),

  listDatasets: () => request<Dataset[]>("GET", "/datasets"),
  listDatasetsPage: (query: PageQuery) => request<Page<Dataset>>("GET", "/datasets", undefined, query),
  getDataset: (id: number) => request<Dataset>("GET", `/datasets/${id}`),
  createDataset: (body: { name: string; description?: string }) => request<Dataset>("POST", "/datasets", body),
  updateDataset: (id: number, body: { name?: string; description?: string }) =>
    request<Dataset>("PATCH", `/datasets/${id}`, body),
  deleteDataset: (id: number) => request<void>("DELETE", `/datasets/${id}`),

  listPassages: (datasetId: number) => request<Passage[]>("GET", `/datasets/${datasetId}/passages`),
  listPassagesPage: (datasetId: number, query: PageQuery) =>
    request<Page<Passage>>("GET", `/datasets/${datasetId}/passages`, undefined, query),
  listTestCases: (datasetId: number) => request<TestCase[]>("GET", `/datasets/${datasetId}/test-cases`),
  listTestCasesPage: (datasetId: number, query: PageQuery) =>
    request<Page<TestCase>>("GET", `/datasets/${datasetId}/test-cases`, undefined, query),
  createTestCase: (datasetId: number, body: TestCaseInput) =>
    request<TestCase>("POST", `/datasets/${datasetId}/test-cases`, body),
  bulkCreateTestCases: (datasetId: number, cases: TestCaseInput[]) =>
    request<TestCase[]>("POST", `/datasets/${datasetId}/test-cases/bulk`, { test_cases: cases }),
  updateTestCase: (id: number, body: Partial<TestCaseInput>) => request<TestCase>("PATCH", `/test-cases/${id}`, body),
  deleteTestCase: (id: number) => request<void>("DELETE", `/test-cases/${id}`),

  listRuns: (query?: { dataset_id?: number; model_name?: string; prompt_version?: string; limit?: number; offset?: number }) =>
    request<RunList>("GET", "/evaluations/runs", undefined, query),
  getRun: (id: number) => request<RunDetail>("GET", `/evaluations/runs/${id}`),
  getRunSummary: (id: number) => request<Run>("GET", `/evaluations/runs/${id}/summary`),
  listRunResults: (id: number, query: PageQuery & { failed_only?: boolean }) =>
    request<Page<Result>>("GET", `/evaluations/runs/${id}/results`, undefined, query),
  createRun: (body: RunCreate) => request<RunDetail>("POST", "/evaluations/runs", body),
  createEvaluationJob: (body: RunCreate, idempotencyKey: string) =>
    request<EvaluationJob>("POST", "/evaluations/jobs", body, undefined, {
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  getEvaluationJob: (id: number, signal?: AbortSignal) =>
    request<EvaluationJob>("GET", `/evaluations/jobs/${id}`, undefined, undefined, { signal }),
  cancelEvaluationJob: (id: number) =>
    request<EvaluationJob>("POST", `/evaluations/jobs/${id}/cancel`),
  createAssessment: <B extends AssessmentCreate>(
    runId: number,
    resultId: number,
    body: B,
    idempotencyKey: string,
  ) =>
    request<
      B extends CitationAssessmentCreate
        ? CitationAssessmentJob
        : B extends ClaimAssessmentCreate
          ? ClaimAssessmentJob
          : AssessmentJob
    >("POST", `/evaluations/runs/${runId}/results/${resultId}/assessments`, body, undefined, {
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  getAssessment: (id: number, signal?: AbortSignal) =>
    request<AssessmentJob>("GET", `/evaluations/assessments/${id}`, undefined, undefined, {
      signal,
    }),
  getAssessmentResult: <T extends AssessmentResult = AssessmentResult>(
    id: number,
    signal?: AbortSignal,
  ) =>
    request<T>("GET", `/evaluations/assessments/${id}/result`, undefined, undefined, { signal }),
  cancelAssessment: (id: number) =>
    request<AssessmentJob>("POST", `/evaluations/assessments/${id}/cancel`),
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

  chatStatus: () => request<ChatStatus>("GET", "/chat/status"),
  listChatSessions: () => request<ChatSession[]>("GET", "/chat/sessions"),
  listChatSessionsPage: (query: PageQuery) =>
    request<Page<ChatSession>>("GET", "/chat/sessions", undefined, query),
  createChatSession: (body: { dataset_id: number; prompt_version: string }) =>
    request<ChatSession>("POST", "/chat/sessions", body),
  getChatSession: (id: number) => request<ChatSessionDetail>("GET", `/chat/sessions/${id}`),
  deleteChatSession: (id: number) => request<void>("DELETE", `/chat/sessions/${id}`),
  sendChatMessage: (sessionId: number, content: string) =>
    request<ChatTurn>("POST", `/chat/sessions/${sessionId}/messages`, { content }),
  saveChatTestCase: (messageId: number, body: SaveChatTestCaseInput) =>
    request<TestCase>("POST", `/chat/messages/${messageId}/test-case`, body),
};
