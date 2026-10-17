import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { NewRun } from "./NewRun";
import { dataset } from "../test/fixtures";

function response(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

function job(status: string, over: Record<string, unknown> = {}) {
  return {
    job_id: 41,
    status,
    status_url: "/evaluations/jobs/41",
    dataset_id: 1,
    dataset_version: 2,
    scoring_input_fingerprint: "f".repeat(64),
    knowledge_base_hash: null,
    progress: { completed: status === "running" ? 1 : 0, total: 2, fraction: status === "running" ? 0.5 : 0 },
    created_at: "2026-10-16T10:00:00Z",
    updated_at: "2026-10-16T10:00:01Z",
    started_at: status === "running" ? "2026-10-16T10:00:01Z" : null,
    heartbeat_at: null,
    finished_at: null,
    attempt_count: 0,
    lease_owner: null,
    lease_expires_at: null,
    cancellation_requested_at: null,
    error: null,
    run_id: status === "succeeded" ? 9 : null,
    retry_of_job_id: null,
    replayed: false,
    ...over,
  };
}

function renderNewRun() {
  function RunPage() {
    const params = useParams();
    return <h1>Run page {params.id}</h1>;
  }
  return render(
    <MemoryRouter initialEntries={["/runs/new?dataset=1"]}>
      <Routes>
        <Route path="/runs/new" element={<NewRun />} />
        <Route path="/runs/:id" element={<RunPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function setupFetch(handler: (method: string, path: string, init?: RequestInit) => Promise<Response> | Response) {
  const calls: { method: string; path: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = init?.method ?? "GET";
    calls.push({ method, path: `${url.pathname}${url.search}`, init });
    if (method === "GET" && url.pathname === "/datasets") return response([dataset]);
    return handler(method, url.pathname, init);
  });
  vi.stubGlobal("fetch", fn);
  return { calls, fn };
}

async function fillForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("Model name"), "model-a");
  await user.type(screen.getByLabelText("Prompt version"), "prompt-1");
  const textarea = screen.getByLabelText("Responses JSON");
  await user.click(textarea);
  await user.paste('[{"case_key":"case-1","output":"answer"}]');
}

beforeEach(() => {
  sessionStorage.clear();
});

describe("NewRun durable job submission", () => {
  it("submits an idempotent job and navigates to its completed run", async () => {
    const user = userEvent.setup();
    const { calls } = setupFetch((method, path, init) => {
      if (method === "POST" && path === "/evaluations/jobs") {
        expect(new Headers(init?.headers).get("Idempotency-Key")).toBeTruthy();
        return response(job("queued"), 202);
      }
      if (method === "GET" && path === "/evaluations/jobs/41") return response(job("succeeded"));
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    renderNewRun();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Queue evaluation" }));

    expect(await screen.findByRole("heading", { name: "Run page 9" })).toBeInTheDocument();
    const submission = calls.find((call) => call.method === "POST" && call.path === "/evaluations/jobs");
    expect(submission).toBeDefined();
    expect(JSON.parse(String(submission?.init?.body))).toMatchObject({ dataset_id: 1, model_name: "model-a" });
  });

  it("reuses the same key after an uncertain network outcome", async () => {
    const user = userEvent.setup();
    const keys: string[] = [];
    let attempts = 0;
    setupFetch((method, path, init) => {
      if (method === "POST" && path === "/evaluations/jobs") {
        keys.push(new Headers(init?.headers).get("Idempotency-Key") ?? "");
        attempts += 1;
        if (attempts === 1) return Promise.reject(new TypeError("network lost"));
        return response(job("queued"), 202);
      }
      if (method === "GET" && path === "/evaluations/jobs/41") return response(job("queued"));
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    renderNewRun();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Queue evaluation" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/reuse its submission key/);
    await user.click(screen.getByRole("button", { name: "Queue evaluation" }));
    expect(await screen.findByText("Queued")).toBeInTheDocument();
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });

  it("keeps the job pending and retries after a status-network failure", async () => {
    const user = userEvent.setup();
    let polls = 0;
    setupFetch((method, path) => {
      if (method === "POST" && path === "/evaluations/jobs") return response(job("queued"), 202);
      if (method === "GET" && path === "/evaluations/jobs/41") {
        polls += 1;
        if (polls === 1) return Promise.reject(new TypeError("temporary offline"));
        return response(job("queued"));
      }
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    renderNewRun();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Queue evaluation" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Status polling will retry automatically/);
    await waitFor(() => expect(polls).toBeGreaterThan(1), { timeout: 4000 });
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.queryByText(/Evaluation could not be completed/)).not.toBeInTheDocument();
  });

  it("shows queued/running progress and confirms cancellation", async () => {
    const user = userEvent.setup();
    let cancelled = false;
    setupFetch((method, path) => {
      if (method === "POST" && path === "/evaluations/jobs") return response(job("queued"), 202);
      if (method === "GET" && path === "/evaluations/jobs/41") {
        return response(cancelled ? job("running", { cancellation_requested_at: "2026-10-16T10:00:02Z" }) : job("running"));
      }
      if (method === "POST" && path === "/evaluations/jobs/41/cancel") {
        cancelled = true;
        return response(job("running", { cancellation_requested_at: "2026-10-16T10:00:02Z" }));
      }
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    vi.stubGlobal("confirm", vi.fn(() => true));
    renderNewRun();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Queue evaluation" }));
    expect(await screen.findByText("Running")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 cases completed")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Evaluation progress" })).toHaveAttribute("value", "50");
    await user.click(screen.getByRole("button", { name: "Cancel evaluation" }));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringMatching(/after the current case/));
    expect(await screen.findByText(/Cancellation requested.*current case/)).toBeInTheDocument();
  });

  it("keeps a queued job pending when the worker has not picked it up", async () => {
    sessionStorage.setItem("evalops.new-run.v1", JSON.stringify({
      draft: { datasetId: "1", modelName: "m", promptVersion: "p", label: "", minF1: "0.5", maxLatency: "", requireExact: false, responsesText: "[]" },
      idempotencyKey: "saved-key",
      requestFingerprint: "saved-fingerprint",
      jobId: 41,
      submittedAt: Date.now() - 31_000,
    }));
    setupFetch((method, path) => {
      if (method === "GET" && path === "/evaluations/jobs/41") return response(job("queued"));
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    renderNewRun();
    expect(await screen.findByText(/worker may be stopped or busy/)).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.queryByText("Evaluation could not be completed.")).not.toBeInTheDocument();
  });

  it("shows failed and cancelled terminal states and starts a new attempt with a new key", async () => {
    const user = userEvent.setup();
    const keys: string[] = [];
    let submissions = 0;
    setupFetch((method, path, init) => {
      if (method === "POST" && path === "/evaluations/jobs") {
        submissions += 1;
        keys.push(new Headers(init?.headers).get("Idempotency-Key") ?? "");
        return response({ ...job("queued"), job_id: submissions, status_url: `/evaluations/jobs/${submissions}` }, 202);
      }
      if (method === "GET" && path === "/evaluations/jobs/1") {
        return response(job("failed", { error: { code: "evaluation_failed", message: "Evaluation could not be completed." } }));
      }
      if (method === "GET" && path === "/evaluations/jobs/2") return response(job("cancelled", { job_id: 2 }));
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    renderNewRun();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Queue evaluation" }));
    expect(await screen.findByText("Evaluation could not be completed.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Start a new attempt" }));
    expect(await screen.findByText("Cancelled")).toBeInTheDocument();
    expect(keys).toHaveLength(2);
    expect(keys[1]).not.toBe(keys[0]);
  });

  it("keeps the form and job identity across refresh without resubmitting", async () => {
    const user = userEvent.setup();
    const handler = (method: string, path: string) => {
      if (method === "POST" && path === "/evaluations/jobs") return response(job("queued"), 202);
      if (method === "GET" && path === "/evaluations/jobs/41") return response(job("queued"));
      throw new Error(`Unexpected request: ${method} ${path}`);
    };
    const { calls } = setupFetch(handler);
    const first = renderNewRun();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Queue evaluation" }));
    expect(await screen.findByText("Queued")).toBeInTheDocument();
    first.unmount();

    renderNewRun();
    expect(await screen.findByText("Queued")).toBeInTheDocument();
    expect(screen.getByLabelText("Model name")).toHaveValue("model-a");
    expect(calls.filter((call) => call.method === "POST" && call.path === "/evaluations/jobs")).toHaveLength(1);
  });

  it("guards double-click submission while the first request is unresolved", async () => {
    const user = userEvent.setup();
    let finish!: (value: Response) => void;
    const { calls } = setupFetch((method, path) => {
      if (method === "POST" && path === "/evaluations/jobs") {
        return new Promise<Response>((resolve) => { finish = resolve; });
      }
      if (method === "GET" && path === "/evaluations/jobs/41") return response(job("queued"));
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    renderNewRun();
    await fillForm(user);
    const button = screen.getByRole("button", { name: "Queue evaluation" });
    await user.click(button);
    await user.click(button);
    expect(calls.filter((call) => call.method === "POST" && call.path === "/evaluations/jobs")).toHaveLength(1);
    finish(response(job("queued"), 202));
    expect(await screen.findByText("Queued")).toBeInTheDocument();
  });

  it("aborts in-flight status polling on unmount and ignores its stale response", async () => {
    const user = userEvent.setup();
    let resolvePoll!: (value: Response) => void;
    let signal: AbortSignal | undefined;
    setupFetch((method, path, init) => {
      if (method === "POST" && path === "/evaluations/jobs") return response(job("queued"), 202);
      if (method === "GET" && path === "/evaluations/jobs/41") {
        signal = init?.signal ?? undefined;
        return new Promise<Response>((resolve) => { resolvePoll = resolve; });
      }
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    const view = renderNewRun();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Queue evaluation" }));
    await waitFor(() => expect(resolvePoll).toBeDefined());
    view.unmount();
    expect(signal?.aborted).toBe(true);
    resolvePoll(response(job("succeeded")));
    await waitFor(() => expect(screen.queryByText("Run page 9")).not.toBeInTheDocument());
  });

  it("does not let an older polling response overwrite a newer cancellation result", async () => {
    const user = userEvent.setup();
    let resolveOldPoll!: (value: Response) => void;
    let firstPoll = true;
    setupFetch((method, path) => {
      if (method === "POST" && path === "/evaluations/jobs") return response(job("queued"), 202);
      if (method === "GET" && path === "/evaluations/jobs/41") {
        if (firstPoll) {
          firstPoll = false;
          return new Promise<Response>((resolve) => { resolveOldPoll = resolve; });
        }
        return response(job("cancelled"));
      }
      if (method === "POST" && path === "/evaluations/jobs/41/cancel") return response(job("cancelled"));
      throw new Error(`Unexpected request: ${method} ${path}`);
    });
    vi.stubGlobal("confirm", vi.fn(() => true));
    renderNewRun();
    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Queue evaluation" }));
    await waitFor(() => expect(resolveOldPoll).toBeDefined());
    await user.click(await screen.findByRole("button", { name: "Cancel evaluation" }));
    expect(await screen.findByText("This evaluation was cancelled.")).toBeInTheDocument();
    resolveOldPoll(response(job("succeeded")));
    await waitFor(() => expect(screen.queryByText("Run page 9")).not.toBeInTheDocument());
    expect(screen.getByText("Cancelled")).toBeInTheDocument();
  });
});
