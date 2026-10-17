import { spawn } from "node:child_process";
import path from "node:path";
import readline from "node:readline";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api/client";
import { ragRun } from "../test/fixtures";
import { RunDetail } from "./RunDetail";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderRunDetail(runId = 7) {
  return render(
    <MemoryRouter initialEntries={[`/runs/${runId}`]}>
      <Routes>
        <Route path="/runs/:id" element={<RunDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RunDetail assessment integration", () => {
  it("does not trigger assessments on load or row expansion and disables controls when no response text was submitted", async () => {
    const user = userEvent.setup();
    const { results, ...summary } = ragRun(7);
    const noOutputResult = {
      ...results[0]!,
      id: 2,
      case_key: "q-no-output",
      output: null,
      passed: false,
      metrics: { responded: false },
      failures: [{ check: "responded", message: "no response submitted" }],
    };

    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input));
        const key = `${init?.method ?? "GET"} ${url.pathname}`;
        calls.push(key);
        if (key === "GET /evaluations/runs/7/summary") return jsonResponse(summary);
        if (key === "GET /evaluations/runs/7/results") {
          return jsonResponse({
            items: [results[0]!, noOutputResult],
            total: 2,
            limit: 50,
            offset: 0,
          });
        }
        throw new Error(`Unexpected request: ${key}`);
      }),
    );

    renderRunDetail(7);
    expect(await screen.findByRole("button", { name: "q-ship-uk" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "q-ship-uk" }));
    expect(
      screen.getByText(/Separate, opt-in frozen assessments.*do not modify deterministic lexical scores/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run citation assessment" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Run claim assessment" })).toBeEnabled();
    expect(calls.some((c) => c.includes("/assessments"))).toBe(false);

    await user.click(screen.getByRole("button", { name: "q-no-output" }));
    expect(
      screen.getByText(
        "Frozen assessments are unavailable because no response text was submitted for this case.",
      ),
    ).toBeInTheDocument();
  });

  it("submits a citation assessment, disables buttons while active, and renders the completed CitationAssessmentResult", async () => {
    const user = userEvent.setup();
    const { results, ...summary } = ragRun(7);
    let resolveAssessmentPost!: (value: Response) => void;
    const submittedBodies: unknown[] = [];

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input));
        const key = `${init?.method ?? "GET"} ${url.pathname}`;
        if (key === "GET /evaluations/runs/7/summary") return jsonResponse(summary);
        if (key === "GET /evaluations/runs/7/results") {
          return jsonResponse({ items: results, total: results.length, limit: 50, offset: 0 });
        }
        if (key === "POST /evaluations/runs/7/results/1/assessments") {
          submittedBodies.push(JSON.parse(String(init?.body)));
          return new Promise<Response>((resolve) => {
            resolveAssessmentPost = resolve;
          });
        }
        if (key === "GET /evaluations/assessments/81") {
          return jsonResponse({
            assessment_id: 81,
            evaluator: "citation",
            status: "succeeded",
            status_url: "/evaluations/assessments/81",
            result_url: "/evaluations/assessments/81/result",
            run_id: 7,
            result_id: 1,
            progress: { completed: 1, total: 1, fraction: 1 },
            evaluator_version: "citation-evaluator-v4",
            predictor_sha256: "p".repeat(64),
            runner_sha256: "r".repeat(64),
            config_digest: "c".repeat(64),
            input_fingerprint: "i".repeat(64),
            knowledge_base_hash: "k".repeat(64),
            created_at: "2026-10-17T00:00:00Z",
            updated_at: "2026-10-17T00:00:01Z",
            started_at: "2026-10-17T00:00:00Z",
            heartbeat_at: null,
            finished_at: "2026-10-17T00:00:01Z",
            attempt_count: 1,
            cancellation_requested: false,
            error: null,
            replayed: false,
          });
        }
        if (key === "GET /evaluations/assessments/81/result") {
          return jsonResponse({
            assessment_id: 81,
            run_id: 7,
            result_id: 1,
            case_key: "q-ship-uk",
            evaluator_version: "citation-evaluator-v4",
            predictor_sha256: "p".repeat(64),
            runner_sha256: "r".repeat(64),
            config_digest: "c".repeat(64),
            input_fingerprint: "i".repeat(64),
            knowledge_base_hash: "k".repeat(64),
            status: "correct",
            reason_code: "supported",
            reason: "Citation KB-ORD-01 matches retrieved passage.",
            per_citation: [
              {
                citation_id: "KB-ORD-01",
                status: "correct",
                reason_code: "supported",
                reason: "Passage covers shipping.",
                component_coverage: [1],
              },
            ],
            created_at: "2026-10-17T00:00:01Z",
          });
        }
        throw new Error(`Unexpected request: ${key}`);
      }),
    );

    renderRunDetail(7);
    await user.click(await screen.findByRole("button", { name: "q-ship-uk" }));

    const citationCheckbox = screen.getByRole("checkbox", { name: "Citation expected" });
    expect(citationCheckbox).toBeChecked();
    await user.click(citationCheckbox);
    expect(citationCheckbox).not.toBeChecked();

    const citeBtn = screen.getByRole("button", { name: "Run citation assessment" });
    const claimBtn = screen.getByRole("button", { name: "Run claim assessment" });
    await user.click(citeBtn);

    expect(citeBtn).toBeDisabled();
    expect(claimBtn).toBeDisabled();
    expect(submittedBodies).toEqual([{ evaluator: "citation", citation_expected: false }]);

    resolveAssessmentPost(
      jsonResponse(
        {
          assessment_id: 81,
          evaluator: "citation",
          status: "queued",
          status_url: "/evaluations/assessments/81",
          result_url: "/evaluations/assessments/81/result",
          run_id: 7,
          result_id: 1,
          progress: { completed: 0, total: 1, fraction: 0 },
          evaluator_version: "citation-evaluator-v4",
          predictor_sha256: "p".repeat(64),
          runner_sha256: "r".repeat(64),
          config_digest: "c".repeat(64),
          input_fingerprint: "i".repeat(64),
          knowledge_base_hash: "k".repeat(64),
          created_at: "2026-10-17T00:00:00Z",
          updated_at: "2026-10-17T00:00:00Z",
          started_at: null,
          heartbeat_at: null,
          finished_at: null,
          attempt_count: 0,
          cancellation_requested: false,
          error: null,
          replayed: false,
        },
        202,
      ),
    );

    expect(await screen.findByRole("table", { name: "Citation decisions" })).toBeInTheDocument();
    expect(screen.getByText("Citation KB-ORD-01 matches retrieved passage.")).toBeInTheDocument();
    expect(citeBtn).toBeEnabled();
    expect(claimBtn).toBeEnabled();
  });

  it("shows running/cancellation states for claim assessment and handles API errors gracefully", async () => {
    const user = userEvent.setup();
    const { results, ...summary } = ragRun(7);
    let resolvePoll!: (value: Response) => void;
    let attempt = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input));
        const key = `${init?.method ?? "GET"} ${url.pathname}`;
        if (key === "GET /evaluations/runs/7/summary") return jsonResponse(summary);
        if (key === "GET /evaluations/runs/7/results") {
          return jsonResponse({ items: results, total: results.length, limit: 50, offset: 0 });
        }
        if (key === "POST /evaluations/runs/7/results/1/assessments") {
          attempt += 1;
          if (attempt === 1) {
            return jsonResponse(
              {
                error: {
                  code: "stale_knowledge_base",
                  message: "The saved run knowledge base differs from the current dataset knowledge base.",
                },
              },
              409,
            );
          }
          return jsonResponse(
            {
              assessment_id: 92,
              evaluator: "claim",
              status: "running",
              status_url: "/evaluations/assessments/92",
              result_url: "/evaluations/assessments/92/result",
              run_id: 7,
              result_id: 1,
              progress: { completed: 1, total: 2, fraction: 0.5 },
              evaluator_version: "semantic-aggregate-v12",
              source_sha256: "s".repeat(64),
              config_digest: "c".repeat(64),
              model_id: "cross-encoder/nli-deberta-v3-small",
              model_revision: "fa2804872c3b4bd748f38c0185cc85775361e735",
              input_fingerprint: "i".repeat(64),
              knowledge_base_hash: "k".repeat(64),
              created_at: "2026-10-17T00:00:00Z",
              updated_at: "2026-10-17T00:00:01Z",
              started_at: "2026-10-17T00:00:01Z",
              heartbeat_at: null,
              finished_at: null,
              attempt_count: 1,
              cancellation_requested: false,
              error: null,
              replayed: false,
            },
            202,
          );
        }
        if (key === "GET /evaluations/assessments/92") {
          return new Promise<Response>((resolve) => {
            resolvePoll = resolve;
          });
        }
        if (key === "POST /evaluations/assessments/92/cancel") {
          return jsonResponse({
            assessment_id: 92,
            evaluator: "claim",
            status: "cancelled",
            status_url: "/evaluations/assessments/92",
            result_url: "/evaluations/assessments/92/result",
            run_id: 7,
            result_id: 1,
            progress: { completed: 1, total: 2, fraction: 0.5 },
            evaluator_version: "semantic-aggregate-v12",
            source_sha256: "s".repeat(64),
            config_digest: "c".repeat(64),
            model_id: "cross-encoder/nli-deberta-v3-small",
            model_revision: "fa2804872c3b4bd748f38c0185cc85775361e735",
            input_fingerprint: "i".repeat(64),
            knowledge_base_hash: "k".repeat(64),
            created_at: "2026-10-17T00:00:00Z",
            updated_at: "2026-10-17T00:00:02Z",
            started_at: "2026-10-17T00:00:01Z",
            heartbeat_at: null,
            finished_at: "2026-10-17T00:00:02Z",
            attempt_count: 1,
            cancellation_requested: true,
            error: null,
            replayed: false,
          });
        }
        throw new Error(`Unexpected request: ${key}`);
      }),
    );

    renderRunDetail(7);
    await user.click(await screen.findByRole("button", { name: "q-ship-uk" }));

    // 1st click returns 409 API error -> displayed in ErrorBanner
    await user.click(screen.getByRole("button", { name: "Run claim assessment" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /saved run knowledge base differs/,
    );

    // 2nd click starts running job -> cancel assessment
    await user.click(screen.getByRole("button", { name: "Run claim assessment" }));
    await waitFor(() => expect(resolvePoll).toBeDefined());
    expect(screen.getByText(/Running assessment/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Cancel assessment" }));
    expect(await screen.findByText("Assessment was cancelled.")).toBeInTheDocument();
  });

  it("disables claim assessment and shows a retrieval metadata explanation when retrieved_passage_ids is null, without disabling citation assessment", async () => {
    const user = userEvent.setup();
    const { results, ...summary } = ragRun(7);
    const noRetrievalResult = {
      ...results[0]!,
      id: 3,
      case_key: "q-no-retrieval",
      output: "Orders can be cancelled within 1 hour.",
      retrieved_passage_ids: null,
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input));
        const key = `${init?.method ?? "GET"} ${url.pathname}`;
        if (key === "GET /evaluations/runs/7/summary") return jsonResponse(summary);
        if (key === "GET /evaluations/runs/7/results") {
          return jsonResponse({ items: [noRetrievalResult], total: 1, limit: 50, offset: 0 });
        }
        throw new Error(`Unexpected request: ${key}`);
      }),
    );

    renderRunDetail(7);
    await user.click(await screen.findByRole("button", { name: "q-no-retrieval" }));

    expect(screen.getByRole("button", { name: "Run citation assessment" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Run claim assessment" })).toBeDisabled();
    expect(
      screen.getByText(/Claim assessment requires recorded retrieval metadata/),
    ).toBeInTheDocument();
  });

  async function startIsolatedBackendBridge() {
    const repoRoot = path.resolve(import.meta.dirname, "../../..");
    const pythonBin = path.join(repoRoot, "backend/.venv/bin/python");

    const bridgeScript = `
import json, sys
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.testclient import TestClient
from evalops.auth import SESSION_COOKIE, _csrf_token_for, _digest
from evalops.config import Settings
from evalops.main import create_app
from evalops.models import AuthSession, User, Workspace, WorkspaceMembership, utcnow
from evalops.semantic.verdict import SemanticVerdict
from evalops.workers.citation_assessment import CitationAssessmentWorker
from evalops.workers.claim_assessment import ClaimAssessmentWorker

class _DeterministicClaimVerifier:
    def verify(self, question, claim, evidence):
        return SemanticVerdict(
            label="supported",
            reason="Direct entailment from refund policy.",
            deciding=[{
                "passage_id": "KB-RET-01",
                "relation": "support",
                "scope": "sentence",
                "unit": evidence.get("KB-RET-01", ""),
            }],
            guards=["fixture_guard"],
            model={"backend": "fixture"},
            config={"question_pass": "not needed", "digest": "92aacfdf47908332"},
        )

with TemporaryDirectory(prefix="evalops-e2e-assess-") as tmp:
    settings = Settings(
        database_url=f"sqlite:///{Path(tmp) / 'test.db'}",
        auto_migrate=True,
        auth_mode="oidc",
        environment="development",
        oidc_issuer="https://idp.example",
        oidc_client_id="evalops-client",
        oidc_redirect_uri="http://127.0.0.1:8000/auth/callback",
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
        cors_origins=["http://127.0.0.1:5173"],
    )
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
        raw_session = "e2e-session-token-12345"
        csrf_token = _csrf_token_for(raw_session)
        s = app.state.session_factory()
        try:
            now = utcnow()
            u = User(issuer="https://idp.example", subject="sub-1", email="dev@example.com", display_name="Dev")
            ws = Workspace(id=2, name="Team Alpha", slug="team-alpha", is_legacy=False)
            s.add_all([u, ws])
            s.flush()
            s.add(WorkspaceMembership(workspace_id=ws.id, user_id=u.id, role="editor"))
            s.add(AuthSession(
                token_hash=_digest(raw_session),
                user_id=u.id,
                active_workspace_id=ws.id,
                csrf_token_hash=_digest(csrf_token),
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(hours=1),
            ))
            s.commit()
        finally:
            s.close()

        client.cookies.set(SESSION_COOKIE, raw_session)
        base_headers = {"X-CSRF-Token": csrf_token, "X-Workspace-ID": "2"}

        ds = client.post("/datasets", headers=base_headers, json={"name": "e2e-rag"}).json()
        client.post(f"/datasets/{ds['id']}/passages", headers=base_headers, json={
            "passages": [{"passage_id": "KB-RET-01", "title": "Refunds", "text": "Refunds are issued to the original payment method within 5 business days."}]
        })
        client.post(f"/datasets/{ds['id']}/test-cases", headers=base_headers, json={
            "key": "refund-method",
            "input": "Where are refunds issued?",
            "expected_output": "original payment method",
            "relevant_passage_ids": ["KB-RET-01"],
        })
        run = client.post("/evaluations/runs", headers=base_headers, json={
            "dataset_id": ds["id"],
            "model_name": "fixture-rag",
            "prompt_version": "rag-v1",
            "responses": [{
                "case_key": "refund-method",
                "output": "Refunds are issued to the original payment method [KB-RET-01].",
                "retrieved_passage_ids": ["KB-RET-01"],
            }],
        }).json()
        gates_before = client.post("/evaluations/gates", headers=base_headers, json={
            "candidate_run_id": run["id"],
            "gates": {"min_pass_rate": 1.0, "min_groundedness": 0.8},
        }).json()

        print(json.dumps({
            "ready": True,
            "run_id": run["id"],
            "csrf_token": csrf_token,
            "workspace_id": 2,
            "summary_before": run["summary"],
            "metrics_before": run["results"][0]["metrics"],
            "gates_before": gates_before,
        }), flush=True)

        for line in sys.stdin:
            cmd = json.loads(line)
            if cmd["type"] == "worker":
                worker = CitationAssessmentWorker(app.state.session_factory)
                processed = worker.run_once()
                print(json.dumps({"processed": processed}), flush=True)
            elif cmd["type"] == "claim_worker":
                worker = ClaimAssessmentWorker(
                    app.state.session_factory,
                    verifier_factory=lambda: _DeterministicClaimVerifier(),
                )
                processed = worker.run_once()
                print(json.dumps({"processed": processed}), flush=True)
            elif cmd["type"] == "http":
                res = client.request(
                    cmd["method"],
                    cmd["path"],
                    headers=cmd.get("headers") or {},
                    content=cmd.get("body"),
                )
                print(json.dumps({
                    "status": res.status_code,
                    "body": res.text,
                    "headers": dict(res.headers),
                }), flush=True)
`;

    const proc = spawn(pythonBin, ["-c", bridgeScript], {
      cwd: path.join(repoRoot, "backend"),
      stdio: ["pipe", "pipe", "inherit"],
    });

    const rl = readline.createInterface({ input: proc.stdout });
    const lines: string[] = [];
    const waiters: ((line: string) => void)[] = [];
    rl.on("line", (line) => {
      const waiter = waiters.shift();
      if (waiter) waiter(line);
      else lines.push(line);
    });
    const nextLine = () =>
      new Promise<string>((resolve) => {
        const buffered = lines.shift();
        if (buffered !== undefined) resolve(buffered);
        else waiters.push(resolve);
      });
    const sendCmd = async <T,>(payload: unknown): Promise<T> => {
      proc.stdin.write(`${JSON.stringify(payload)}\n`);
      return JSON.parse(await nextLine()) as T;
    };

    const initInfo = JSON.parse(await nextLine()) as {
      ready: boolean;
      run_id: number;
      csrf_token: string;
      workspace_id: number;
      summary_before: unknown;
      metrics_before: unknown;
      gates_before: unknown;
    };

    return {
      initInfo,
      sendCmd,
      close: () => {
        rl.close();
        proc.kill();
      },
    };
  }

  it("completes an end-to-end citation assessment against an isolated FastAPI backend and deterministic worker while preserving headers, lexical metrics, and release gates", async () => {
    const user = userEvent.setup();
    const bridge = await startIsolatedBackendBridge();

    try {
      const { initInfo, sendCmd } = bridge;
      expect(initInfo.ready).toBe(true);

      const capturedAssessmentPostHeaders: Record<string, string>[] = [];
      let statusPollCount = 0;

      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
          const url = new URL(String(input));
          const method = init?.method ?? "GET";
          const pathAndQuery = `${url.pathname}${url.search}`;
          const headersObj: Record<string, string> = {};
          new Headers(init?.headers).forEach((value, key) => {
            headersObj[key] = value;
          });

          if (method === "POST" && url.pathname.endsWith("/assessments")) {
            capturedAssessmentPostHeaders.push(headersObj);
          }

          // After the UI sees the first "queued" status poll, execute the deterministic citation worker once
          if (method === "GET" && /^\/evaluations\/assessments\/\d+$/.test(url.pathname)) {
            statusPollCount += 1;
            if (statusPollCount === 2) {
              const workerOut = await sendCmd<{ processed: number | null }>({ type: "worker" });
              expect(workerOut.processed).toBe(1);
            }
          }

          const res = await sendCmd<{
            status: number;
            body: string;
            headers: Record<string, string>;
          }>({
            type: "http",
            method,
            path: pathAndQuery,
            headers: headersObj,
            body: typeof init?.body === "string" ? init.body : undefined,
          });
          return new Response(res.status === 204 ? null : res.body, {
            status: res.status,
            headers: { "Content-Type": res.headers["content-type"] ?? "application/json" },
          });
        }),
      );

      // Load real OIDC session (sets CSRF token and activeWorkspaceId = 2 in api client)
      const session = await api.authSession();
      expect(session.authenticated).toBe(true);
      expect(session.csrf_token).toBe(initInfo.csrf_token);
      expect(session.active_workspace_id).toBe(2);

      renderRunDetail(initInfo.run_id);
      await user.click(await screen.findByRole("button", { name: "refund-method" }));

      // Submit citation assessment and observe queued state before worker runs on 2nd poll
      await user.click(screen.getByRole("button", { name: "Run citation assessment" }));
      expect(await screen.findByText(/Queued for worker/)).toBeInTheDocument();

      // Verify completed result rendered from the real CitationAssessmentWorker + FastAPI backend
      expect(
        await screen.findByRole("table", { name: "Citation decisions" }, { timeout: 4000 }),
      ).toBeInTheDocument();
      expect(screen.getByText(/citation_set_has_appropriate_passage/)).toBeInTheDocument();
      expect(screen.getByText("KB-RET-01")).toBeInTheDocument();

      // Verify authentication, CSRF, workspace, and Idempotency-Key headers sent on POST
      expect(capturedAssessmentPostHeaders).toHaveLength(1);
      expect(capturedAssessmentPostHeaders[0]?.["x-csrf-token"]).toBe(initInfo.csrf_token);
      expect(capturedAssessmentPostHeaders[0]?.["x-workspace-id"]).toBe("2");
      expect(capturedAssessmentPostHeaders[0]?.["idempotency-key"]).toBeTruthy();

      // Confirm lexical metrics and release-gate evaluation are unchanged in the backend
      const runAfter = await api.getRun(initInfo.run_id);
      expect(runAfter.summary).toEqual(initInfo.summary_before);
      expect(runAfter.results[0]?.metrics).toEqual(initInfo.metrics_before);

      const gatesAfter = await api.evaluateGates({
        candidate_run_id: initInfo.run_id,
        gates: { min_pass_rate: 1.0, min_groundedness: 0.8 },
      });
      expect(gatesAfter).toEqual(initInfo.gates_before);
    } finally {
      bridge.close();
    }
  });

  it("completes an end-to-end semantic claim assessment via ClaimAssessmentService and ClaimAssessmentWorker with a stubbed verifier_factory while preserving lexical metrics and release gates", async () => {
    const user = userEvent.setup();
    const bridge = await startIsolatedBackendBridge();

    try {
      const { initInfo, sendCmd } = bridge;
      expect(initInfo.ready).toBe(true);

      const capturedAssessmentPosts: { headers: Record<string, string>; body: string | undefined }[] =
        [];
      const capturedResultPayloads: Record<string, unknown>[] = [];
      let statusPollCount = 0;

      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
          const url = new URL(String(input));
          const method = init?.method ?? "GET";
          const pathAndQuery = `${url.pathname}${url.search}`;
          const headersObj: Record<string, string> = {};
          new Headers(init?.headers).forEach((value, key) => {
            headersObj[key] = value;
          });

          if (method === "POST" && url.pathname.endsWith("/assessments")) {
            capturedAssessmentPosts.push({
              headers: headersObj,
              body: typeof init?.body === "string" ? init.body : undefined,
            });
          }

          // After the UI sees the first "queued" status poll, run ClaimAssessmentWorker with stubbed verifier_factory
          if (method === "GET" && /^\/evaluations\/assessments\/\d+$/.test(url.pathname)) {
            statusPollCount += 1;
            if (statusPollCount === 2) {
              const workerOut = await sendCmd<{ processed: number | null }>({
                type: "claim_worker",
              });
              expect(workerOut.processed).toBe(1);
            }
          }

          const res = await sendCmd<{
            status: number;
            body: string;
            headers: Record<string, string>;
          }>({
            type: "http",
            method,
            path: pathAndQuery,
            headers: headersObj,
            body: typeof init?.body === "string" ? init.body : undefined,
          });

          if (method === "GET" && /^\/evaluations\/assessments\/\d+\/result$/.test(url.pathname)) {
            capturedResultPayloads.push(JSON.parse(res.body) as Record<string, unknown>);
          }

          return new Response(res.status === 204 ? null : res.body, {
            status: res.status,
            headers: { "Content-Type": res.headers["content-type"] ?? "application/json" },
          });
        }),
      );

      // Load real OIDC session (sets CSRF token and activeWorkspaceId = 2 in api client)
      const session = await api.authSession();
      expect(session.authenticated).toBe(true);
      expect(session.csrf_token).toBe(initInfo.csrf_token);
      expect(session.active_workspace_id).toBe(2);

      renderRunDetail(initInfo.run_id);
      await user.click(await screen.findByRole("button", { name: "refund-method" }));

      // Submit semantic claim assessment from the eligible RAG result and observe queued state
      await user.click(screen.getByRole("button", { name: "Run claim assessment" }));
      expect(await screen.findByText(/Queued for worker/)).toBeInTheDocument();

      // Verify completed result retrieved and rendered by ClaimAssessmentResultCard
      const claimTable = await screen.findByRole(
        "table",
        { name: "Claim verdicts" },
        { timeout: 4000 },
      );
      expect(claimTable).toBeInTheDocument();
      expect(screen.getByTestId("claim-assessment-disclaimer")).toHaveTextContent(
        "semantic-aggregate-v12",
      );
      expect(
        screen.getByText("cross-encoder/nli-deberta-v3-small (fa2804872c3b)"),
      ).toBeInTheDocument();
      expect(screen.getByText("assessed")).toBeInTheDocument();
      expect(
        within(claimTable).getByText("Refunds are issued to the original payment method."),
      ).toBeInTheDocument();
      expect(within(claimTable).getByText("supported")).toBeInTheDocument();
      expect(
        within(claimTable).getByText("Direct entailment from refund policy."),
      ).toBeInTheDocument();
      expect(within(claimTable).getByText(/guards: fixture_guard/)).toBeInTheDocument();

      const evidenceList = within(claimTable).getByRole("list", { name: "Deciding evidence" });
      expect(evidenceList).toHaveTextContent("KB-RET-01");
      expect(evidenceList).toHaveTextContent(
        "support · sentence · Refunds are issued to the original payment method within 5 business days.",
      );

      // Verify POST request body and headers
      expect(capturedAssessmentPosts).toHaveLength(1);
      expect(JSON.parse(capturedAssessmentPosts[0]?.body ?? "{}")).toEqual({
        evaluator: "claim",
      });
      expect(capturedAssessmentPosts[0]?.headers["x-csrf-token"]).toBe(initInfo.csrf_token);
      expect(capturedAssessmentPosts[0]?.headers["x-workspace-id"]).toBe("2");
      expect(capturedAssessmentPosts[0]?.headers["idempotency-key"]).toBeTruthy();

      // Verify actual backend ClaimAssessmentResultOut contract and evaluator metadata
      expect(capturedResultPayloads).toHaveLength(1);
      expect(capturedResultPayloads[0]).toMatchObject({
        assessment_id: 1,
        run_id: initInfo.run_id,
        result_id: 1,
        case_key: "refund-method",
        evaluator_version: "semantic-aggregate-v12",
        model_id: "cross-encoder/nli-deberta-v3-small",
        model_revision: "fa2804872c3b4bd748f38c0185cc85775361e735",
        status: "assessed",
        claims: [
          {
            claim_text: "Refunds are issued to the original payment method.",
            label: "supported",
            abstained: false,
            reason: "Direct entailment from refund policy.",
            diagnostic_branch: "fixture_guard",
            guards: ["fixture_guard"],
            deciding_evidence: [
              {
                passage_id: "KB-RET-01",
                relation: "support",
                scope: "sentence",
                unit: "Refunds are issued to the original payment method within 5 business days.",
              },
            ],
          },
        ],
      });

      // Confirm lexical metrics, run summary, and release-gate evaluations remain unchanged
      const runAfter = await api.getRun(initInfo.run_id);
      expect(runAfter.summary).toEqual(initInfo.summary_before);
      expect(runAfter.results[0]?.metrics).toEqual(initInfo.metrics_before);

      const gatesAfter = await api.evaluateGates({
        candidate_run_id: initInfo.run_id,
        gates: { min_pass_rate: 1.0, min_groundedness: 0.8 },
      });
      expect(gatesAfter).toEqual(initInfo.gates_before);
    } finally {
      bridge.close();
    }
  });
});


