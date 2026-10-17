import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import type { ReactNode } from "react";
import type { Dataset, Page, Passage, Result, TestCase } from "../api/types";
import { Datasets } from "./Datasets";
import { DatasetDetail } from "./DatasetDetail";
import { RunDetail } from "./RunDetail";
import { ragRun, run } from "../test/fixtures";

const pageSize = 50;
const date = "2026-10-15T00:00:00Z";

function dataset(id: number, name = `dataset-${id}`): Dataset {
  return {
    id,
    name,
    description: "Synthetic dataset",
    version: 1,
    created_at: date,
    updated_at: date,
    test_case_count: 51,
    run_count: 1,
    passage_count: 51,
  };
}

function testCase(id: number, key = `case-${id}`): TestCase {
  return {
    id,
    dataset_id: 1,
    key,
    input: `question ${key}`,
    expected_output: "answer",
    required_terms: [],
    forbidden_terms: [],
    json_schema: null,
    max_latency_ms: null,
    tags: [],
    relevant_passage_ids: [],
    expect_abstain: false,
    version: 1,
    content_hash: "a".repeat(64),
    created_at: date,
    updated_at: date,
  };
}

function passage(id: number): Passage {
  return {
    passage_id: `P-${id}`,
    title: `Passage ${id}`,
    text: `Evidence ${id}`,
    created_at: date,
    updated_at: date,
  };
}

function result(key: string, id = 1): Result {
  return { ...ragRun(1).results[0]!, id, case_key: key };
}

function paged<T>(items: T[], total: number, offset: number): Page<T> {
  return { items, total, limit: pageSize, offset };
}

type FetchCall = { path: string; search: string };
type RouteHandler = (url: URL) => unknown | Promise<unknown>;

function mockRoutes(routes: Record<string, unknown | RouteHandler>) {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      calls.push({ path: url.pathname, search: url.search });
      const route = routes[url.pathname];
      if (route === undefined) {
        return new Response(
          JSON.stringify({ error: { code: "not_found", message: url.pathname } }),
          { status: 404 },
        );
      }
      const data = typeof route === "function" ? await route(url) : route;
      if (data instanceof Response) return data;
      return new Response(JSON.stringify(data), { status: 200 });
    }),
  );
  return calls;
}

function renderAt(initialPath: string, element: ReactNode, routePath = initialPath) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path={routePath} element={element} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("datasets pagination", () => {
  it("uses bounded pages with Previous/Next and keeps dataset navigation links", async () => {
    const calls = mockRoutes({
      "/datasets": (url: URL) => {
        const offset = Number(url.searchParams.get("offset"));
        const length = Math.min(50, 101 - offset);
        const items = Array.from({ length }, (_, index) =>
          dataset(offset + index + 1, `dataset-${String(offset + index).padStart(2, "0")}`),
        );
        return paged(items, 101, offset);
      },
    });
    renderAt("/datasets", <Datasets />);

    expect(await screen.findByText("1–50 of 101")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "dataset-00" })).toHaveAttribute("href", "/datasets/1");
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByRole("link", { name: "dataset-50" })).toBeInTheDocument();
    expect(screen.getByText("51–100 of 101")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByRole("link", { name: "dataset-100" })).toBeInTheDocument();
    expect(screen.getByText("101–101 of 101")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await screen.findByRole("link", { name: "dataset-50" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await screen.findByRole("link", { name: "dataset-00" })).toBeInTheDocument();
    expect(calls.every((call) => call.search.includes("limit=50"))).toBe(true);
  });

  it("renders an empty page and a request error", async () => {
    mockRoutes({ "/datasets": paged([], 0, 0) });
    renderAt("/datasets", <Datasets />);
    expect(await screen.findByText("No datasets yet.")).toBeInTheDocument();
    expect(screen.getByText("0–0 of 0")).toBeInTheDocument();

    mockRoutes({
      "/datasets": new Response(
        JSON.stringify({ error: { code: "offline", message: "dataset service unavailable" } }),
        { status: 503 },
      ),
    });
    renderAt("/datasets", <Datasets />);
    expect(await screen.findByRole("alert")).toHaveTextContent("dataset service unavailable");
  });
});

describe("dataset detail pagination", () => {
  it("pages cases and passages while preserving expanded case state and counts", async () => {
    const calls = mockRoutes({
      "/datasets/1": dataset(1, "main"),
      "/datasets/1/test-cases": (url: URL) => {
        const offset = Number(url.searchParams.get("offset"));
        const length = Math.min(50, 101 - offset);
        return paged(
          Array.from({ length }, (_, index) =>
            testCase(offset + index + 1, `case-${String(offset + index).padStart(3, "0")}`),
          ),
          101,
          offset,
        );
      },
      "/datasets/1/passages": (url: URL) => {
        const offset = Number(url.searchParams.get("offset"));
        const length = Math.min(50, 101 - offset);
        return paged(Array.from({ length }, (_, index) => passage(offset + index)), 101, offset);
      },
    });
    renderAt("/datasets/1", <DatasetDetail />, "/datasets/:id");

    expect(await screen.findByText("Test cases (101)")).toBeInTheDocument();
    expect(screen.getByText("Knowledge base (101 passages)")).toBeInTheDocument();
    let caseCard = screen.getByText("Test cases (101)").closest("section")!;
    await userEvent.click(within(caseCard).getByRole("button", { name: "case-000" }));
    expect(within(caseCard).getAllByText("question case-000")).toHaveLength(2);
    await userEvent.click(within(caseCard).getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(calls.some((call) => call.path.endsWith("test-cases") && call.search.includes("offset=50"))).toBe(true));
    expect(await screen.findByText("51–100 of 101")).toBeInTheDocument();
    caseCard = screen.getByText("Test cases (101)").closest("section")!;
    expect(await within(caseCard).findByRole("button", { name: "case-050" })).toBeInTheDocument();
    await userEvent.click(within(caseCard).getByRole("button", { name: "Next page" }));
    expect(await screen.findByRole("button", { name: "case-100" })).toBeInTheDocument();
    expect(screen.getByText("101–101 of 101")).toBeInTheDocument();
    caseCard = screen.getByText("Test cases (101)").closest("section")!;
    await userEvent.click(within(caseCard).getByRole("button", { name: "Previous page" }));
    expect(await screen.findByRole("button", { name: "case-050" })).toBeInTheDocument();
    caseCard = screen.getByText("Test cases (101)").closest("section")!;
    await userEvent.click(within(caseCard).getByRole("button", { name: "Previous page" }));
    const expanded = await screen.findByRole("button", { name: "case-000" });
    expect(expanded).toHaveAttribute("aria-expanded", "true");

    await userEvent.click(screen.getByRole("button", { name: "Show knowledge base passages" }));
    const passageCard = screen.getByText("Knowledge base (101 passages)").closest("section")!;
    await userEvent.click(within(passageCard).getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("P-50")).toBeInTheDocument();
    expect(calls.filter((call) => call.path.includes("test-cases") || call.path.includes("passages"))
      .every((call) => call.search.includes("limit=50"))).toBe(true);
  });

  it("ignores a stale dataset page after navigating to another dataset", async () => {
    let resolveOld!: (value: unknown) => void;
    function Navigation() {
      const navigate = useNavigate();
      return <button onClick={() => navigate("/datasets/2")}>Open dataset two</button>;
    }
    const calls = mockRoutes({
      "/datasets/1": dataset(1, "first dataset"),
      "/datasets/2": dataset(2, "second dataset"),
      "/datasets/1/test-cases": () => new Promise((resolve) => { resolveOld = resolve; }),
      "/datasets/1/passages": paged([passage(1)], 1, 0),
      "/datasets/2/test-cases": paged([testCase(2, "second-case")], 1, 0),
      "/datasets/2/passages": paged([passage(2)], 1, 0),
    });
    render(
      <MemoryRouter initialEntries={["/datasets/1"]}>
        <Navigation />
        <Routes><Route path="/datasets/:id" element={<DatasetDetail />} /></Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(resolveOld).toBeTypeOf("function"));
    await userEvent.click(await screen.findByRole("button", { name: "Open dataset two" }));
    expect(await screen.findByText("second dataset")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "second-case" })).toBeInTheDocument();
    resolveOld(paged([testCase(1, "stale-first-case")], 1, 0));
    await waitFor(() => expect(screen.queryByRole("button", { name: "stale-first-case" })).not.toBeInTheDocument());
    expect(calls.some((call) => call.path === "/datasets/1/test-cases")).toBe(true);
  });
});

describe("run result pagination", () => {
  it("handles empty and failed result-page requests", async () => {
    mockRoutes({
      "/evaluations/runs/1/summary": run(1),
      "/evaluations/runs/1/results": paged([], 0, 0),
    });
    renderAt("/runs/1", <RunDetail />, "/runs/:id");
    expect(await screen.findByText("This run has no results.")).toBeInTheDocument();
    expect(screen.getByText("0–0 of 0")).toBeInTheDocument();

    mockRoutes({
      "/evaluations/runs/1/summary": run(1),
      "/evaluations/runs/1/results": new Response(
        JSON.stringify({ error: { code: "offline", message: "results unavailable" } }),
        { status: 503 },
      ),
    });
    renderAt("/runs/1", <RunDetail />, "/runs/:id");
    expect(await screen.findByRole("alert")).toHaveTextContent("results unavailable");
  });

  it("pages results, filters failed results server-side, and avoids the unbounded detail route", async () => {
    const baseResult = ragRun(1).results[0]!;
    const all = Array.from({ length: 101 }, (_, index) => ({ ...baseResult, id: index + 1, case_key: `case-${String(index).padStart(3, "0")}` }));
    const failed = [{ ...baseResult, id: 99, case_key: "failed-case", passed: false }];
    const calls = mockRoutes({
      "/evaluations/runs/1/summary": run(1),
      "/evaluations/runs/1/results": (url: URL) => {
        if (url.searchParams.get("failed_only") === "true") return paged(failed, 1, 0);
        const offset = Number(url.searchParams.get("offset"));
        return paged(all.slice(offset, offset + pageSize), all.length, offset);
      },
    });
    renderAt("/runs/1", <RunDetail />, "/runs/:id");

    expect(await screen.findByRole("button", { name: "case-000" })).toBeInTheDocument();
    expect(screen.getByText("1–50 of 101")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "case-000" }));
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByRole("button", { name: "case-050" })).toBeInTheDocument();
    expect(screen.getByText("51–100 of 101")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByRole("button", { name: "case-100" })).toBeInTheDocument();
    expect(screen.getByText("101–101 of 101")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await screen.findByRole("button", { name: "case-050" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await screen.findByRole("button", { name: "case-000" })).toHaveAttribute("aria-expanded", "true");

    await userEvent.click(screen.getByRole("checkbox", { name: "Failed only" }));
    expect(await screen.findByRole("button", { name: "failed-case" })).toBeInTheDocument();
    expect(screen.getByText("1–1 of 1")).toBeInTheDocument();
    expect(calls.some((call) => call.path === "/evaluations/runs/1")).toBe(false);
    expect(calls.every((call) => call.path !== "/evaluations/runs/1/results" || call.search.includes("limit=50"))).toBe(true);
  });

  it("ignores old result responses after switching runs", async () => {
    let resolveOld!: (value: unknown) => void;
    function Navigation() {
      const navigate = useNavigate();
      return <button onClick={() => navigate("/runs/2")}>Open run two</button>;
    }
    mockRoutes({
      "/evaluations/runs/1/summary": run(1),
      "/evaluations/runs/2/summary": run(2),
      "/evaluations/runs/1/results": () => new Promise((resolve) => { resolveOld = resolve; }),
      "/evaluations/runs/2/results": paged([result("run-two-result")], 1, 0),
    });
    render(
      <MemoryRouter initialEntries={["/runs/1"]}>
        <Navigation />
        <Routes><Route path="/runs/:id" element={<RunDetail />} /></Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(resolveOld).toBeTypeOf("function"));
    await userEvent.click(await screen.findByRole("button", { name: "Open run two" }));
    expect(await screen.findByRole("button", { name: "run-two-result" })).toBeInTheDocument();
    resolveOld(paged([result("stale-run-one-result")], 1, 0));
    await waitFor(() => expect(screen.queryByRole("button", { name: "stale-run-one-result" })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Run #2" })).toBeInTheDocument();
  });
});
