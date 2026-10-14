import { expect, test as setup, type APIRequestContext } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { API_URL } from "./environment";

const examples = path.resolve(import.meta.dirname, "../../examples");
const read = (file: string) => JSON.parse(readFileSync(path.join(examples, file), "utf8"));

async function post(request: APIRequestContext, endpoint: string, data: unknown) {
  const response = await request.post(`${API_URL}${endpoint}`, { data });
  expect(response.ok(), `${endpoint}: ${await response.text()}`).toBe(true);
  return response.json();
}

// Empty states are checked before seeding. All later tests depend on this one setup,
// so ordering, retries and reused databases cannot change the demo baseline.
setup("fresh database empty states and recorded demo seed", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await expect(page.getByText("No evaluation runs yet.", { exact: false })).toBeVisible();
  await page.getByRole("link", { name: "Create a dataset", exact: true }).click();
  await expect(page.getByText("No datasets yet.")).toBeVisible();
  await page.goto("/runs");
  await expect(page.getByText("No runs match.")).toBeVisible();
  await page.goto("/runs/new");
  await expect(page.getByRole("button", { name: "Download template" })).toBeDisabled();

  const support = read("datasets/kestrel_support_v1.json");
  const ds = await post(request, "/datasets", { name: support.name, description: support.description });
  await post(request, `/datasets/${ds.id}/test-cases/bulk`, { test_cases: support.test_cases });
  for (const file of ["baseline_prod_v1.3", "candidate_v1.4_regressed", "candidate_v1.5_fixed"]) {
    await post(request, "/evaluations/runs", { ...read(`responses/${file}.json`), dataset_id: ds.id });
  }

  // Exercise the actual demo twice in the isolated test database: replay only,
  // with idempotent seeding and no discovery of unrelated experimental recordings.
  const root = path.resolve(examples, "..");
  for (let attempt = 0; attempt < 2; attempt++) {
    const output = execFileSync(path.join(root, "backend/.venv/bin/python"), [
      path.join(root, "backend/scripts/rag_demo.py"), "--base-url", API_URL, "--judge", "replay",
    ], { encoding: "utf8" });
    expect(output).toContain("methodology 2.0.0");
    expect(output).toContain("Comparison rag-v1 -> rag-v3: compatible=True");
    expect(output).toMatch(/\(rag-v3\): NOT_READY - 5 passed, 3 failed, 1 skipped/);
  }
  const runs = await (await request.get(`${API_URL}/evaluations/runs?limit=200`)).json();
  const ragRuns = runs.items.filter((run: { prompt_version: string }) => run.prompt_version.startsWith("rag-"));
  expect(ragRuns.map((run: { prompt_version: string }) => run.prompt_version).sort()).toEqual(["rag-v1", "rag-v2", "rag-v3"]);
  for (const run of ragRuns) {
    expect(run.methodology_version).toBe("2.0.0");
    if (["rag-v1", "rag-v3"].includes(run.prompt_version)) {
      expect(run.summary.passed_cases).toBe(9);
      expect(run.summary.total_cases).toBe(20);
    }
  }
  const datasets = await (await request.get(`${API_URL}/datasets`)).json();
  expect(datasets.map((d: { name: string }) => d.name).sort()).toEqual(["kestrel-policy-rag-v1", "kestrel-support-v1"]);
});
