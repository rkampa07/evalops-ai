import { ApiError, api } from "./client";

describe("api client", () => {
  it("parses the backend error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: { code: "conflict", message: "dataset exists" } }), { status: 409 })),
    );
    const err = await api.createDataset({ name: "x" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "conflict", message: "dataset exists" });
  });

  it("reports network failures clearly", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(api.health()).rejects.toMatchObject({ code: "network_error" });
  });

  it("handles non-JSON error bodies", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>bad gateway</html>", { status: 502 })));
    await expect(api.health()).rejects.toMatchObject({ status: 502, message: "Request failed (HTTP 502)" });
  });

  it("returns undefined for 204 and sends JSON bodies", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.deleteRun(3)).resolves.toBeUndefined();
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toMatch(/\/evaluations\/runs\/3$/);
  });

  it("omits empty query params", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 })));
    vi.stubGlobal("fetch", fetchMock);
    await api.listRuns({ dataset_id: 2, model_name: undefined });
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toMatch(/\/evaluations\/runs\?dataset_id=2$/);
  });

  it("sends the durable job idempotency key", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      void _input;
      void _init;
      return new Response(JSON.stringify({ job_id: 5 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    await api.createEvaluationJob(
      { dataset_id: 1, model_name: "m", prompt_version: "p", responses: [{ case_key: "a", output: "x" }] },
      "stable-key",
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe("stable-key");
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(/\/evaluations\/jobs$/);
    expect(init.credentials).toBe("include");
  });

  it("loads a CSRF token and sends it on state-changing requests", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      void _input;
      void _init;
      return new Response(
        JSON.stringify({
          authenticated: true,
          mode: "oidc",
          user: { id: 3, issuer: "issuer", subject: "subject" },
          csrf_token: "csrf-test",
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    await api.authSession();
    await api.deleteRun(4);
    const init = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-test");
    expect(init.credentials).toBe("include");
  });

  it("persists the selected workspace header and clears workspace session state on switch", async () => {
    window.sessionStorage.setItem("workspace-draft", "private state");
    const session = {
      authenticated: true,
      mode: "oidc" as const,
      user: { id: 3, issuer: "issuer", subject: "subject" },
      csrf_token: "csrf-test",
      workspaces: [
        { id: 4, name: "Alpha", role: "owner" as const, is_legacy: false },
        { id: 5, name: "Beta", role: "editor" as const, is_legacy: false },
      ],
      active_workspace_id: 4,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void init;
      const path = new URL(String(input)).pathname;
      if (path === "/auth/session/workspace") {
        return new Response(JSON.stringify({ ...session, active_workspace_id: 5 }));
      }
      if (path === "/auth/session") return new Response(JSON.stringify(session));
      return new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    await api.authSession();
    await api.selectWorkspace(5);
    expect(window.sessionStorage.getItem("workspace-draft")).toBeNull();
    await api.listDatasets();
    const request = fetchMock.mock.calls.at(-1)?.[1] as RequestInit;
    expect(new Headers(request.headers).get("X-Workspace-ID")).toBe("5");
  });

  it("submits citation and claim assessments with Idempotency-Key, CSRF, and workspace headers", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input)).pathname;
      if (path === "/auth/session") {
        return new Response(
          JSON.stringify({
            authenticated: true,
            mode: "oidc",
            user: { id: 3, issuer: "issuer", subject: "subject" },
            csrf_token: "csrf-assess",
            active_workspace_id: 9,
          }),
        );
      }
      const parsed = JSON.parse(String(init?.body ?? "{}")) as { evaluator: string };
      return new Response(
        JSON.stringify({
          assessment_id: parsed.evaluator === "citation" ? 11 : 12,
          evaluator: parsed.evaluator,
          status: "queued",
          status_url: "/evaluations/assessments/11",
          result_url: "/evaluations/assessments/11/result",
          run_id: 7,
          result_id: 42,
          progress: { completed: 0, total: 1, fraction: 0 },
        }),
        { status: 202 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.authSession();
    const citationJob = await api.createAssessment(
      7,
      42,
      { evaluator: "citation", citation_expected: true },
      "cite-key-1",
    );
    expect(citationJob.evaluator).toBe("citation");
    expect(citationJob.assessment_id).toBe(11);

    const citationCall = fetchMock.mock.calls[1] as [RequestInfo | URL, RequestInit];
    expect(String(citationCall[0])).toMatch(/\/evaluations\/runs\/7\/results\/42\/assessments$/);
    expect(citationCall[1].method).toBe("POST");
    expect(citationCall[1].credentials).toBe("include");
    const citationHeaders = new Headers(citationCall[1].headers);
    expect(citationHeaders.get("Content-Type")).toBe("application/json");
    expect(citationHeaders.get("Idempotency-Key")).toBe("cite-key-1");
    expect(citationHeaders.get("X-CSRF-Token")).toBe("csrf-assess");
    expect(citationHeaders.get("X-Workspace-ID")).toBe("9");
    expect(JSON.parse(String(citationCall[1].body))).toEqual({
      evaluator: "citation",
      citation_expected: true,
    });

    const claimJob = await api.createAssessment(7, 42, { evaluator: "claim" }, "claim-key-1");
    expect(claimJob.evaluator).toBe("claim");
    expect(claimJob.assessment_id).toBe(12);

    const claimCall = fetchMock.mock.calls[2] as [RequestInfo | URL, RequestInit];
    expect(String(claimCall[0])).toMatch(/\/evaluations\/runs\/7\/results\/42\/assessments$/);
    expect(new Headers(claimCall[1].headers).get("Idempotency-Key")).toBe("claim-key-1");
    expect(JSON.parse(String(claimCall[1].body))).toEqual({ evaluator: "claim" });
  });

  it("fetches assessment status, results, and cancels assessments", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void init;
      const path = new URL(String(input)).pathname;
      if (path === "/evaluations/assessments/15") {
        return new Response(
          JSON.stringify({
            assessment_id: 15,
            evaluator: "claim",
            status: "succeeded",
            status_url: "/evaluations/assessments/15",
            result_url: "/evaluations/assessments/15/result",
            run_id: 7,
            result_id: 42,
            progress: { completed: 2, total: 2, fraction: 1 },
          }),
        );
      }
      if (path === "/evaluations/assessments/15/result") {
        return new Response(
          JSON.stringify({
            assessment_id: 15,
            run_id: 7,
            result_id: 42,
            case_key: "case-1",
            evaluator_version: "semantic-aggregate-v12",
            source_sha256: "abc",
            config_digest: "def",
            model_id: "cross-encoder/nli-deberta-v3-small",
            model_revision: "rev1",
            input_fingerprint: "fp1",
            knowledge_base_hash: "kb1",
            status: "assessed",
            claims: [
              {
                claim_text: "Refunds take 5 business days.",
                label: "supported",
                abstained: false,
                reason: "Direct entailment",
                diagnostic_branch: "evidence_only",
                deciding_evidence: [{ passage_id: "KB-RET-01", relation: "entails" }],
                guards: [],
                config: {},
                model: {},
                uncalibrated_scores: [],
                ignored_contradictions: [],
              },
            ],
            created_at: "2026-10-17T00:00:00Z",
          }),
        );
      }
      return new Response(
        JSON.stringify({
          assessment_id: 15,
          evaluator: "claim",
          status: "cancelled",
          cancellation_requested: true,
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const status = await api.getAssessment(15, controller.signal);
    expect(status.status).toBe("succeeded");
    const statusCall = fetchMock.mock.calls[0] as [RequestInfo | URL, RequestInit];
    expect(String(statusCall[0])).toMatch(/\/evaluations\/assessments\/15$/);
    expect(statusCall[1].method).toBe("GET");
    expect(statusCall[1].signal).toBe(controller.signal);

    const result = await api.getAssessmentResult(15, controller.signal);
    expect("claims" in result && result.claims[0]?.label).toBe("supported");
    const resultCall = fetchMock.mock.calls[1] as [RequestInfo | URL, RequestInit];
    expect(String(resultCall[0])).toMatch(/\/evaluations\/assessments\/15\/result$/);
    expect(resultCall[1].method).toBe("GET");
    expect(resultCall[1].signal).toBe(controller.signal);

    const cancelled = await api.cancelAssessment(15);
    expect(cancelled.status).toBe("cancelled");
    const cancelCall = fetchMock.mock.calls[2] as [RequestInfo | URL, RequestInit];
    expect(String(cancelCall[0])).toMatch(/\/evaluations\/assessments\/15\/cancel$/);
    expect(cancelCall[1].method).toBe("POST");
  });
});
