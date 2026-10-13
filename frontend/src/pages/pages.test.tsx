import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { App } from "../App";
import { comparison, dataset, incomparable, mockFetch, ragRun, report, run, summary } from "../test/fixtures";
import { parseResponses } from "./NewRun";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("Overview", () => {
  it("shows stats computed from API data", async () => {
    mockFetch({
      "GET /datasets": [dataset],
      "GET /evaluations/runs": { items: [run(2), run(1, { pass_rate: 11 / 12, summary: summary({ passed_cases: 11 }) })], total: 2, limit: 20, offset: 0 },
    });
    renderAt("/");
    expect(await screen.findByText("Latest pass rate")).toBeInTheDocument();
    expect(screen.getByText("Latest pass rate").nextElementSibling).toHaveTextContent("66.7%");
    expect(screen.getByText("12 test cases")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Pass rate by run" })).toBeInTheDocument();
  });

  it("shows an actionable error when the API is down", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    renderAt("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("Is the backend running?");
  });

  it("shows an empty state with no runs", async () => {
    mockFetch({ "GET /datasets": [], "GET /evaluations/runs": { items: [], total: 0, limit: 20, offset: 0 } });
    renderAt("/");
    expect(await screen.findByText(/No evaluation runs yet/)).toBeInTheDocument();
  });
});

describe("Compare", () => {
  it("renders regression verdict and case changes", async () => {
    mockFetch({
      "GET /evaluations/runs": { items: [run(2), run(1)], total: 2, limit: 200, offset: 0 },
      "GET /evaluations/compare": comparison,
    });
    renderAt("/compare?baseline=1&candidate=2");
    expect(await screen.findByText("Regression detected")).toBeInTheDocument();
    expect(screen.getByText("safety-unauthorized-compensation")).toBeInTheDocument();
    expect(screen.getByText("−25.0 pts")).toBeInTheDocument();
  });
});

describe("Compare compatibility", () => {
  it("shows 'not comparable' instead of a regression verdict", async () => {
    mockFetch({
      "GET /evaluations/runs": { items: [run(2), run(1)], total: 2, limit: 200, offset: 0 },
      "GET /evaluations/compare": incomparable,
    });
    renderAt("/compare?baseline=1&candidate=2");
    expect(await screen.findByTestId("compare-verdict")).toHaveTextContent("Not comparable");
    expect(screen.getByText(/min_token_f1: 0.3 -> 0.6/)).toBeInTheDocument();
    expect(screen.queryByText("Regression detected")).not.toBeInTheDocument();
    expect(screen.queryByText("Metric deltas")).not.toBeInTheDocument();
  });

  it("lists comparison warnings alongside a valid verdict", async () => {
    mockFetch({
      "GET /evaluations/runs": { items: [run(2), run(1)], total: 2, limit: 200, offset: 0 },
      "GET /evaluations/compare": {
        ...comparison,
        comparable_cases: 11,
        compatibility_issues: [{ severity: "warning", kind: "definition_changed", message: "1 case(s) changed definition" }],
      },
    });
    renderAt("/compare?baseline=1&candidate=2");
    expect(await screen.findByText("Regression detected")).toBeInTheDocument();
    expect(screen.getByText(/Compared 11 case/)).toBeInTheDocument();
    expect(screen.getByText("1 case(s) changed definition")).toBeInTheDocument();
  });

  it("report shows n/a regressed cases for an incomparable baseline", async () => {
    mockFetch({
      "GET /evaluations/runs": { items: [run(2), run(1)], total: 2, limit: 200, offset: 0 },
      "GET /evaluations/runs/2/report": { ...report("not_ready"), comparison: incomparable },
    });
    renderAt("/reports?run=2&baseline=1");
    await screen.findByTestId("verdict");
    expect(screen.getByText("Regressed cases").nextElementSibling).toHaveTextContent("n/a");
    expect(screen.getByText("Baseline not comparable")).toBeInTheDocument();
  });
});

describe("RAG run detail", () => {
  it("separates deterministic evidence from estimated model scores", async () => {
    mockFetch({ "GET /evaluations/runs/7": ragRun(7) });
    renderAt("/runs/7");
    expect(await screen.findByText("RAG evidence")).toBeInTheDocument();
    expect(screen.getByText("deterministic")).toBeInTheDocument();
    expect(screen.getByText("estimated, not ground truth")).toBeInTheDocument();
    expect(screen.getByText("Estimated groundedness").nextElementSibling).toHaveTextContent("50.0%");
    expect(screen.getByText("Generation metadata")).toBeInTheDocument();
    expect(screen.getByText("mlx-community/Qwen2.5-1.5B-Instruct-4bit")).toBeInTheDocument();
  });

  it("shows heuristic and judge verdicts per claim", async () => {
    mockFetch({ "GET /evaluations/runs/7": ragRun(7) });
    renderAt("/runs/7");
    await userEvent.click(await screen.findByRole("button", { name: "q-ship-uk" }));
    const row = screen.getByText("Yes, you can ship your order to London in the UK.").closest("tr")!;
    expect(row).toHaveTextContent("unsupported");
    expect(screen.getByText(/Retrieved: KB-ORD-02, KB-RET-03, KB-ORD-01/)).toBeInTheDocument();
  });

  it("keeps estimated deltas out of the comparison verdict", async () => {
    mockFetch({
      "GET /evaluations/runs": { items: [run(2), run(1)], total: 2, limit: 200, offset: 0 },
      "GET /evaluations/compare": {
        ...comparison,
        estimated_metric_deltas: [
          { metric: "estimated_groundedness", baseline: 0.44, candidate: 0.3, delta: -0.14, direction: "higher_is_better", status: "regressed" },
        ],
        estimated_note: "estimated by judge; not ground truth and not part of has_regression",
      },
    });
    renderAt("/compare?baseline=1&candidate=2");
    expect(await screen.findByText("estimated, not part of the verdict")).toBeInTheDocument();
    expect(screen.getByText("Groundedness (estimated by judge)")).toBeInTheDocument();
  });
});

describe("Reports", () => {
  it.each([
    ["not_ready", "NOT READY FOR RELEASE"],
    ["ready", "READY FOR RELEASE"],
  ] as const)("shows %s verdict", async (readiness, text) => {
    const { calls } = mockFetch({
      "GET /evaluations/runs": { items: [run(2), run(1)], total: 2, limit: 200, offset: 0 },
      "GET /evaluations/runs/2/report": report(readiness),
    });
    renderAt("/reports?run=2&baseline=1");
    expect(await screen.findByTestId("verdict")).toHaveTextContent(text);
    expect(calls).toContain("GET /evaluations/runs/2/report?baseline_run_id=1");
  });

  it("evaluates a custom gate policy via the API", async () => {
    const { fn } = mockFetch({
      "GET /evaluations/runs": { items: [run(2), run(1)], total: 2, limit: 200, offset: 0 },
      "GET /evaluations/runs/2/report": report("not_ready"),
      "POST /evaluations/gates": { ...report("not_ready").gate_evaluation, decision: "fail" },
    });
    renderAt("/reports?run=2&baseline=1");
    await screen.findByTestId("verdict");
    await userEvent.click(screen.getByRole("button", { name: "Evaluate gates" }));
    expect(await screen.findByText("Custom policy: FAIL")).toBeInTheDocument();
    const post = fn.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post![1] as RequestInit).body))).toEqual({
      candidate_run_id: 2,
      baseline_run_id: 1,
      gates: { min_pass_rate: 0.95, max_forbidden_violations: 0 },
    });
  });
});

describe("NewRun", () => {
  it("parses both response file shapes", () => {
    expect(parseResponses('[{"case_key":"a","output":"x"}]').responses).toHaveLength(1);
    const f = parseResponses('{"model_name":"m","responses":[{"case_key":"a","output":"x","latency_ms":5}]}');
    expect(f.model_name).toBe("m");
    expect(() => parseResponses('{"responses": []}')).toThrow(/non-empty/);
    expect(() => parseResponses('[{"case_key": 1, "output": "x"}]')).toThrow(/Response 1/);
    expect(() => parseResponses("nope")).toThrow(SyntaxError);
  });

  it("prefills fields from an uploaded response file and submits", async () => {
    const { fn } = mockFetch({
      "GET /datasets": [dataset],
      "POST /evaluations/runs": { ...run(9), results: [] },
      "GET /evaluations/runs/9": { ...run(9), results: [] },
    });
    renderAt("/runs/new?dataset=1");
    const textarea = await screen.findByLabelText("Responses JSON");
    await waitFor(() => expect(screen.getByRole("option", { name: /kestrel-support-v1/ })).toBeInTheDocument());
    const body = JSON.stringify({ model_name: "m-x", prompt_version: "v9", responses: [{ case_key: "a", output: "hi", input: "ignored" }] });
    await userEvent.click(textarea);
    await userEvent.paste(body);
    expect(screen.getByLabelText("Model name")).toHaveValue("m-x");
    await userEvent.click(screen.getByRole("button", { name: "Run evaluation" }));
    expect(await screen.findByText("Run #9")).toBeInTheDocument();
    const post = fn.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "POST");
    const sent = JSON.parse(String((post![1] as RequestInit).body));
    expect(sent.responses).toEqual([{ case_key: "a", output: "hi", latency_ms: null }]);
    expect(sent.dataset_id).toBe(1);
  });
});
