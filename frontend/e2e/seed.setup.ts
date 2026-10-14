import { expect, test as setup, type APIRequestContext } from "@playwright/test";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
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

  const rag = read("rag/kestrel_policy_kb.json");
  const kb = await post(request, "/datasets", { name: rag.name, description: rag.description });
  await post(request, `/datasets/${kb.id}/passages`, { passages: rag.passages });
  await post(request, `/datasets/${kb.id}/test-cases/bulk`, { test_cases: rag.test_cases });
  // Replay committed recordings only. Additional local recordings are supported
  // without invoking a generator or modifying fixtures or human-review labels.
  const files = readdirSync(path.join(examples, "rag/responses"))
    .filter((file) => /^qwen2\.5-1\.5b_rag-v\d+\.json$/.test(file)).sort();
  expect(files).toEqual(expect.arrayContaining(["qwen2.5-1.5b_rag-v1.json", "qwen2.5-1.5b_rag-v2.json"]));
  for (const file of files) {
    const record = read(`rag/responses/${file}`);
    const run = await post(request, "/evaluations/runs", {
      dataset_id: kb.id,
      model_name: record.model_name,
      prompt_version: record.prompt_version,
      label: record.label,
      config: record.config,
      generation: record.generation,
      responses: record.responses,
    });
    const grades = read(`rag/grades/llama3.2-3b_judge-v2_on_qwen-${record.prompt_version}.json`);
    const response = await request.put(`${API_URL}/evaluations/runs/${run.id}/model-grades`, {
      data: { judge: grades.judge, grades: grades.grades },
    });
    expect(response.ok(), await response.text()).toBe(true);
  }
  const datasets = await (await request.get(`${API_URL}/datasets`)).json();
  expect(datasets.map((d: { name: string }) => d.name).sort()).toEqual(["kestrel-policy-rag-v1", "kestrel-support-v1"]);
});
