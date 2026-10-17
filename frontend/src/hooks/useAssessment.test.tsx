import { act, renderHook, waitFor } from "@testing-library/react";
import type {
  CitationAssessmentJob,
  CitationAssessmentResult,
  ClaimAssessmentJob,
  ClaimAssessmentResult,
} from "../api/types";
import { useAssessment } from "./useAssessment";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeCitationJob(
  status: CitationAssessmentJob["status"],
  overrides: Partial<CitationAssessmentJob> = {},
): CitationAssessmentJob {
  return {
    assessment_id: 51,
    evaluator: "citation",
    status,
    status_url: "/evaluations/assessments/51",
    result_url: "/evaluations/assessments/51/result",
    run_id: 3,
    result_id: 14,
    progress: {
      completed: status === "succeeded" ? 1 : 0,
      total: 1,
      fraction: status === "succeeded" ? 1 : 0,
    },
    evaluator_version: "citation-evaluator-v4",
    predictor_sha256: "p".repeat(64),
    runner_sha256: "r".repeat(64),
    config_digest: "c".repeat(64),
    input_fingerprint: "i".repeat(64),
    knowledge_base_hash: "k".repeat(64),
    created_at: "2026-10-17T00:00:00Z",
    updated_at: "2026-10-17T00:00:01Z",
    started_at: status === "queued" ? null : "2026-10-17T00:00:01Z",
    heartbeat_at: null,
    finished_at: status === "succeeded" ? "2026-10-17T00:00:02Z" : null,
    attempt_count: 1,
    cancellation_requested: false,
    error: null,
    replayed: false,
    ...overrides,
  };
}

function makeClaimJob(
  status: ClaimAssessmentJob["status"],
  overrides: Partial<ClaimAssessmentJob> = {},
): ClaimAssessmentJob {
  return {
    assessment_id: 62,
    evaluator: "claim",
    status,
    status_url: "/evaluations/assessments/62",
    result_url: "/evaluations/assessments/62/result",
    run_id: 3,
    result_id: 14,
    progress: {
      completed: status === "succeeded" ? 2 : 1,
      total: 2,
      fraction: status === "succeeded" ? 1 : 0.5,
    },
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
    finished_at: status === "succeeded" ? "2026-10-17T00:00:02Z" : null,
    attempt_count: 1,
    cancellation_requested: false,
    error: null,
    replayed: false,
    ...overrides,
  };
}

const citationResultFixture: CitationAssessmentResult = {
  assessment_id: 51,
  run_id: 3,
  result_id: 14,
  case_key: "case-1",
  evaluator_version: "citation-evaluator-v4",
  predictor_sha256: "p".repeat(64),
  runner_sha256: "r".repeat(64),
  config_digest: "c".repeat(64),
  input_fingerprint: "i".repeat(64),
  knowledge_base_hash: "k".repeat(64),
  status: "correct",
  reason_code: "all_citations_supported",
  reason: "All citations match retrieved evidence.",
  per_citation: [
    {
      citation_id: "KB-RET-01",
      status: "correct",
      reason_code: "supported",
      reason: "Supported by KB-RET-01.",
      component_coverage: [1],
    },
  ],
  created_at: "2026-10-17T00:00:02Z",
};

const claimResultFixture: ClaimAssessmentResult = {
  assessment_id: 62,
  run_id: 3,
  result_id: 14,
  case_key: "case-1",
  evaluator_version: "semantic-aggregate-v12",
  source_sha256: "s".repeat(64),
  config_digest: "c".repeat(64),
  model_id: "cross-encoder/nli-deberta-v3-small",
  model_revision: "fa2804872c3b4bd748f38c0185cc85775361e735",
  input_fingerprint: "i".repeat(64),
  knowledge_base_hash: "k".repeat(64),
  status: "assessed",
  claims: [
    {
      claim_text: "Refunds take 5 business days.",
      label: "supported",
      abstained: false,
      reason: "Entailed by passage.",
      diagnostic_branch: "evidence_only",
      deciding_evidence: [{ passage_id: "KB-RET-01", relation: "entails" }],
      guards: [],
      config: {},
      model: {},
      uncalibrated_scores: [],
      ignored_contradictions: [],
    },
  ],
  created_at: "2026-10-17T00:00:02Z",
};

describe("useAssessment hook", () => {
  it("submits a citation assessment, polls queued -> running -> succeeded, and fetches the result only when succeeded", async () => {
    const calls: string[] = [];
    let pollCount = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input)).pathname;
        const method = init?.method ?? "GET";
        calls.push(`${method} ${path}`);

        if (method === "POST" && path === "/evaluations/runs/3/results/14/assessments") {
          return jsonResponse(makeCitationJob("queued"), 202);
        }
        if (method === "GET" && path === "/evaluations/assessments/51") {
          pollCount += 1;
          return jsonResponse(makeCitationJob(pollCount === 1 ? "running" : "succeeded"));
        }
        if (method === "GET" && path === "/evaluations/assessments/51/result") {
          expect(pollCount).toBeGreaterThanOrEqual(2);
          return jsonResponse(citationResultFixture);
        }
        throw new Error(`Unexpected request: ${method} ${path}`);
      }),
    );

    const { result } = renderHook(() =>
      useAssessment(3, 14, { firstPollMs: 10, maxPollMs: 20 }),
    );

    await act(async () => {
      await result.current.submit({ evaluator: "citation", citation_expected: true });
    });

    await waitFor(() => expect(result.current.result).toEqual(citationResultFixture));
    expect(result.current.status).toBe("succeeded");
    expect(result.current.isTerminal).toBe(true);
    expect(result.current.isActive).toBe(false);
    expect(calls).toEqual([
      "POST /evaluations/runs/3/results/14/assessments",
      "GET /evaluations/assessments/51",
      "GET /evaluations/assessments/51",
      "GET /evaluations/assessments/51/result",
    ]);
  });

  it("retries result fetching on 409 result-not-ready and recovers from transient status poll errors", async () => {
    let statusAttempts = 0;
    let resultAttempts = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input)).pathname;
        const method = init?.method ?? "GET";

        if (method === "POST" && path === "/evaluations/runs/3/results/14/assessments") {
          return jsonResponse(makeClaimJob("running"), 202);
        }
        if (method === "GET" && path === "/evaluations/assessments/62") {
          statusAttempts += 1;
          if (statusAttempts === 1) {
            throw new TypeError("transient network drop");
          }
          return jsonResponse(makeClaimJob("succeeded"));
        }
        if (method === "GET" && path === "/evaluations/assessments/62/result") {
          resultAttempts += 1;
          if (resultAttempts === 1) {
            return jsonResponse(
              { error: { code: "conflict", message: "claim assessment result is not available yet" } },
              409,
            );
          }
          return jsonResponse(claimResultFixture);
        }
        throw new Error(`Unexpected request: ${method} ${path}`);
      }),
    );

    const { result } = renderHook(() =>
      useAssessment(3, 14, { firstPollMs: 10, maxPollMs: 20 }),
    );

    await act(async () => {
      await result.current.submit({ evaluator: "claim" });
    });

    await waitFor(() => expect(result.current.result).toEqual(claimResultFixture));
    expect(statusAttempts).toBe(2);
    expect(resultAttempts).toBe(2);
    expect(result.current.pollError).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("reuses the same Idempotency-Key after an uncertain network failure and generates a new key for a new request or after terminal completion", async () => {
    const keys: string[] = [];
    let submitCount = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input)).pathname;
        const method = init?.method ?? "GET";

        if (method === "POST" && path === "/evaluations/runs/3/results/14/assessments") {
          submitCount += 1;
          keys.push(new Headers(init?.headers).get("Idempotency-Key") ?? "");
          if (submitCount === 1) {
            throw new TypeError("connection reset");
          }
          return jsonResponse(
            makeCitationJob("failed", {
              assessment_id: submitCount,
              error: { code: "stale_knowledge_base", message: "KB changed." },
            }),
            202,
          );
        }
        throw new Error(`Unexpected request: ${method} ${path}`);
      }),
    );

    const { result } = renderHook(() =>
      useAssessment(3, 14, { firstPollMs: 10, maxPollMs: 20 }),
    );

    // 1st attempt fails with network error
    await act(async () => {
      await result.current.submit({ evaluator: "citation", citation_expected: true });
    });
    expect(result.current.error).toMatch(/Cannot reach the EvalOps API/);

    // Retry unchanged request -> reuses key
    await act(async () => {
      await result.current.submit({ evaluator: "citation", citation_expected: true });
    });
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
    expect(result.current.status).toBe("failed");
    expect(result.current.error).toBe("KB changed.");

    // New attempt after terminal failure -> new key
    await act(async () => {
      await result.current.submit({ evaluator: "citation", citation_expected: true });
    });
    expect(keys[2]).toBeTruthy();
    expect(keys[2]).not.toBe(keys[1]);
  });

  it("cancels an active assessment and prevents a stale poll response from overwriting the cancellation state", async () => {
    let resolveStalePoll!: (value: Response) => void;
    let firstPoll = true;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input)).pathname;
        const method = init?.method ?? "GET";

        if (method === "POST" && path === "/evaluations/runs/3/results/14/assessments") {
          return jsonResponse(makeCitationJob("queued"), 202);
        }
        if (method === "GET" && path === "/evaluations/assessments/51") {
          if (firstPoll) {
            firstPoll = false;
            return new Promise<Response>((resolve) => {
              resolveStalePoll = resolve;
            });
          }
          return jsonResponse(makeCitationJob("cancelled", { cancellation_requested: true }));
        }
        if (method === "POST" && path === "/evaluations/assessments/51/cancel") {
          return jsonResponse(makeCitationJob("cancelled", { cancellation_requested: true }));
        }
        throw new Error(`Unexpected request: ${method} ${path}`);
      }),
    );

    const { result } = renderHook(() =>
      useAssessment(3, 14, { firstPollMs: 10, maxPollMs: 20 }),
    );

    await act(async () => {
      await result.current.submit({ evaluator: "citation", citation_expected: false });
    });
    await waitFor(() => expect(resolveStalePoll).toBeDefined());

    await act(async () => {
      await result.current.cancel();
    });
    expect(result.current.status).toBe("cancelled");

    // Resolve the older poll with "succeeded" after cancellation completed
    await act(async () => {
      resolveStalePoll(jsonResponse(makeCitationJob("succeeded")));
    });

    expect(result.current.status).toBe("cancelled");
    expect(result.current.result).toBeNull();
  });

  it("aborts in-flight polling on unmount and resets state when target resultId changes", async () => {
    let pollSignal: AbortSignal | undefined;
    let resolvePoll!: (value: Response) => void;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input)).pathname;
        const method = init?.method ?? "GET";

        if (method === "POST" && path === "/evaluations/runs/3/results/14/assessments") {
          return jsonResponse(makeClaimJob("queued"), 202);
        }
        if (method === "GET" && path === "/evaluations/assessments/62") {
          pollSignal = init?.signal ?? undefined;
          return new Promise<Response>((resolve) => {
            resolvePoll = resolve;
          });
        }
        throw new Error(`Unexpected request: ${method} ${path}`);
      }),
    );

    const { result, rerender, unmount } = renderHook(
      ({ runId, resultId }) => useAssessment(runId, resultId, { firstPollMs: 10, maxPollMs: 20 }),
      { initialProps: { runId: 3, resultId: 14 } },
    );

    await act(async () => {
      await result.current.submit({ evaluator: "claim" });
    });
    await waitFor(() => expect(pollSignal).toBeDefined());
    const firstSignal = pollSignal;

    // Changing resultId aborts the active poll and resets hook state
    rerender({ runId: 3, resultId: 99 });
    expect(firstSignal?.aborted).toBe(true);
    expect(result.current.job).toBeNull();
    expect(result.current.status).toBeNull();

    unmount();
    resolvePoll(jsonResponse(makeClaimJob("succeeded")));
  });

  it("allows a second assessment submission after a completed first assessment without the old polling effect invalidating it", async () => {
    const keys: string[] = [];
    const calls: string[] = [];
    let resolveSecondSubmit!: (value: Response) => void;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input)).pathname;
        const method = init?.method ?? "GET";
        calls.push(`${method} ${path}`);

        if (method === "POST" && path === "/evaluations/runs/3/results/14/assessments") {
          keys.push(new Headers(init?.headers).get("Idempotency-Key") ?? "");
          const body = JSON.parse(String(init?.body)) as { evaluator: string };
          if (body.evaluator === "citation") {
            return jsonResponse(makeCitationJob("queued"), 202);
          }
          return new Promise<Response>((resolve) => {
            resolveSecondSubmit = resolve;
          });
        }
        if (method === "GET" && path === "/evaluations/assessments/51") {
          return jsonResponse(makeCitationJob("succeeded"));
        }
        if (method === "GET" && path === "/evaluations/assessments/51/result") {
          return jsonResponse(citationResultFixture);
        }
        if (method === "GET" && path === "/evaluations/assessments/62") {
          return jsonResponse(makeClaimJob("succeeded"));
        }
        if (method === "GET" && path === "/evaluations/assessments/62/result") {
          return jsonResponse(claimResultFixture);
        }
        throw new Error(`Unexpected request: ${method} ${path}`);
      }),
    );

    const { result } = renderHook(() =>
      useAssessment(3, 14, { firstPollMs: 10, maxPollMs: 20 }),
    );

    // 1. Complete citation assessment
    await act(async () => {
      await result.current.submit({ evaluator: "citation", citation_expected: true });
    });
    await waitFor(() => expect(result.current.result).toEqual(citationResultFixture));
    expect(result.current.status).toBe("succeeded");

    const callsBeforeSecond = calls.length;

    // 2. Start a second assessment (claim) whose POST resolves asynchronously after re-render
    let secondPromise!: Promise<unknown>;
    act(() => {
      secondPromise = result.current.submit({ evaluator: "claim" });
    });
    await waitFor(() => expect(resolveSecondSubmit).toBeDefined());

    // Ensure the old assessment (#51) was NOT re-polled while the second submission is in flight
    expect(calls.slice(callsBeforeSecond)).toEqual([
      "POST /evaluations/runs/3/results/14/assessments",
    ]);
    expect(result.current.job).toBeNull();
    expect(result.current.result).toBeNull();

    await act(async () => {
      resolveSecondSubmit(jsonResponse(makeClaimJob("queued"), 202));
      await secondPromise;
    });

    await waitFor(() => expect(result.current.result).toEqual(claimResultFixture));
    expect(result.current.job?.assessment_id).toBe(62);
    expect(result.current.status).toBe("succeeded");
    expect(keys).toHaveLength(2);
    expect(keys[1]).not.toBe(keys[0]);
  });

  it("handles a 409 Conflict when cancelling an already-completed assessment by refreshing terminal status without setting pollError, while preserving genuine cancel errors", async () => {
    let cancelStatus = 409;
    let jobTerminal = true;
    let resolveInitialPoll!: (value: Response) => void;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input)).pathname;
        const method = init?.method ?? "GET";

        if (method === "POST" && path === "/evaluations/runs/3/results/14/assessments") {
          return jsonResponse(makeCitationJob("running"), 202);
        }
        if (method === "GET" && path === "/evaluations/assessments/51") {
          if (!jobTerminal) {
            return new Promise<Response>((resolve) => {
              resolveInitialPoll = resolve;
            });
          }
          return jsonResponse(makeCitationJob("succeeded"));
        }
        if (method === "GET" && path === "/evaluations/assessments/51/result") {
          return jsonResponse(citationResultFixture);
        }
        if (method === "POST" && path === "/evaluations/assessments/51/cancel") {
          if (cancelStatus === 409) {
            jobTerminal = true;
            return jsonResponse(
              { error: { code: "conflict", message: "citation assessment is already succeeded" } },
              409,
            );
          }
          return jsonResponse(
            { error: { code: "internal_error", message: "Cancel service error" } },
            500,
          );
        }
        throw new Error(`Unexpected request: ${method} ${path}`);
      }),
    );

    const { result } = renderHook(() =>
      useAssessment(3, 14, { firstPollMs: 50, maxPollMs: 100 }),
    );

    jobTerminal = false;
    await act(async () => {
      await result.current.submit({ evaluator: "citation", citation_expected: true });
    });
    await waitFor(() => expect(resolveInitialPoll).toBeDefined());

    // 1. Genuine 500 error on cancel -> sets pollError
    cancelStatus = 500;
    await act(async () => {
      await result.current.cancel();
    });
    expect(result.current.pollError).toBe("Cancel service error");

    // 2. 409 Conflict on cancel because job just finished -> no pollError, refreshes to succeeded + loads result
    cancelStatus = 409;
    await act(async () => {
      await result.current.cancel();
    });
    expect(result.current.pollError).toBeNull();
    await waitFor(() => expect(result.current.result).toEqual(citationResultFixture));
    expect(result.current.status).toBe("succeeded");
    expect(result.current.pollError).toBeNull();
  });
});

