import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { API_URL } from "./environment";

const candidateFile = path.resolve(import.meta.dirname, "../../examples/responses/candidate_v1.4_regressed.json");

test("dashboard shows seeded activity and navigates to run history", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Pass rate by run (last 20)" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recent runs" })).toBeVisible();
  await expect(page.getByRole("row", { name: /v1.4.*Candidate/ })).toContainText("kestrel-support-v1");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("link", { name: "All runs" }).click();
  await expect(page.getByRole("heading", { name: "Run history" })).toBeVisible();
});

test("run history opens candidate results, failures and recorded output", async ({ page }) => {
  await page.goto("/runs");
  const candidate = page.getByRole("row", { name: /v1.4.*Candidate/ });
  await expect(candidate).toContainText("8/12");
  await candidate.getByRole("link", { name: /^#\d+$/ }).click();
  await expect(page.getByRole("heading", { name: /^Run #\d+$/ })).toBeVisible();
  await expect(page.getByText("scoring methodology", { exact: false })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Results (12)", exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: "Failed only" }).check();
  await expect(page.getByRole("heading", { name: "Results (4)", exact: true })).toBeVisible();
  const failure = page.getByRole("row", { name: /safety-unauthorized-compensation/ });
  await expect(failure).toContainText("fail");
  await expect(failure).toContainText("forbidden");
  await failure.getByRole("button", { name: "safety-unauthorized-compensation" }).click();
  await expect(page.getByText("So sorry! We guarantee a $500 credit to your account today.", { exact: true })).toBeVisible();
});

test("run filters return the real API empty state and recover", async ({ page }) => {
  await page.goto("/runs");
  await expect(page.getByRole("row", { name: /v1.4.*Candidate/ })).toBeVisible();
  await page.getByLabel("Model", { exact: true }).fill("e2e-nonexistent-model");
  await page.getByLabel("Model", { exact: true }).press("Tab");
  await expect(page.getByText("No runs match.")).toBeVisible();
  await page.getByLabel("Model", { exact: true }).fill("");
  await page.getByLabel("Model", { exact: true }).press("Tab");
  await expect(page.getByRole("row", { name: /v1.4.*Candidate/ })).toBeVisible();
});

test("fixed candidate comparison has no regressions", async ({ page }) => {
  await page.goto("/compare");
  const baseline = page.getByLabel("Baseline run");
  await expect(baseline.locator("option", { hasText: "v1.3 — Production baseline" })).toHaveCount(1);
  await baseline.selectOption({ label: await baseline.locator("option", { hasText: "v1.3 — Production baseline" }).innerText() });
  const candidate = page.getByLabel("Candidate run");
  await expect(candidate.locator("option", { hasText: "v1.5 — Candidate" })).toHaveCount(1);
  await candidate.selectOption({ label: await candidate.locator("option", { hasText: "v1.5 — Candidate" }).innerText() });
  await expect(page.getByTestId("compare-verdict")).toHaveText("No regression detected");
  await expect(page.getByText("regressed: 0", { exact: true })).toBeVisible();
});

test("comparison and report explain missing run selections", async ({ page }) => {
  await page.goto("/compare");
  await expect(page.getByText("Pick a baseline and a candidate run.", { exact: false })).toBeVisible();
  await expect(page.getByTestId("compare-verdict")).toHaveCount(0);
  await page.goto("/reports");
  await expect(page.getByText("Select a candidate run to generate its release-readiness report.")).toBeVisible();
  await expect(page.getByTestId("verdict")).toHaveCount(0);
});

for (const resource of ["datasets", "runs"]) {
  test(`missing ${resource} displays the real API 404`, async ({ page }) => {
    const expectedSuffix = resource === "runs" ? "/evaluations/runs/999999/summary" : "/datasets/999999";
    const response = page.waitForResponse((r) => r.url().endsWith(expectedSuffix) && r.status() === 404);
    await page.goto(`/${resource}/999999`);
    await response;
    await expect(page.getByRole("alert")).toContainText("not found");
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  });
}

test("unknown frontend route offers navigation back to the dashboard", async ({ page }) => {
  await page.goto("/nonexistent-e2e-page");
  await expect(page.getByText("Page not found.", { exact: false })).toBeVisible();
  await page.getByRole("link", { name: "Back to overview" }).click();
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
});

test("network failure shows an actionable error and Retry recovers", async ({ page }) => {
  // Only this failure is simulated; recovery and all happy paths use the real API.
  const datasetsUrl = `${API_URL}/datasets`;
  await page.route(datasetsUrl, (route) => route.abort("connectionfailed"));
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Cannot reach the EvalOps API");
  await page.unroute(datasetsUrl);
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recent runs" })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("new evaluation rejects invalid JSON and unknown case keys", async ({ page }) => {
  await page.goto("/runs/new");
  await expect(page.getByLabel("Dataset").locator("option", { hasText: "kestrel-support-v1" })).toHaveCount(1);
  await page.getByLabel("Dataset").selectOption({ label: "kestrel-support-v1 (12 cases)" });
  await page.getByLabel("Model name", { exact: true }).fill("e2e-validation");
  await page.getByLabel("Prompt version", { exact: true }).fill("e2e-validation");
  await page.getByLabel("Responses JSON", { exact: true }).fill("invalid JSON");
  await page.getByRole("button", { name: "Queue evaluation", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("Responses are not valid JSON.");
  await page.getByLabel("Responses JSON", { exact: true }).fill('[{"case_key":"nonexistent-e2e-case","output":"recorded response"}]');
  const response = page.waitForResponse((r) => r.url().endsWith("/evaluations/jobs") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Queue evaluation", exact: true }).click();
  expect((await response).status()).toBe(422);
  await expect(page.getByRole("alert")).toContainText("nonexistent-e2e-case");
  await expect(page).toHaveURL(/\/runs\/new$/);
});

test("recorded JSON file creates an evaluation through the browser", async ({ page, request }) => {
  // Use an existing recording without changing scoring policy or repository data.
  // Clean up our temporary run even after assertion failures or repeat executions.
  let runId: number | undefined;
  try {
    await page.goto("/runs/new");
    await expect(page.getByLabel("Dataset").locator("option", { hasText: "kestrel-support-v1" })).toHaveCount(1);
    await page.getByLabel("Dataset").selectOption({ label: "kestrel-support-v1 (12 cases)" });
    await page.getByLabel("Responses JSON file", { exact: true }).setInputFiles(candidateFile);
    const recording = JSON.parse(readFileSync(candidateFile, "utf8"));
    await expect(page.getByLabel("Model name", { exact: true })).toHaveValue(recording.model_name);
    const response = page.waitForResponse((r) => r.url().endsWith("/evaluations/jobs") && r.request().method() === "POST");
    await page.getByRole("button", { name: "Queue evaluation", exact: true }).click();
    const created = await response;
    expect(created.status()).toBe(202);
    const queuedJob = await created.json();
    await expect(page).toHaveURL(/\/runs\/\d+$/);
    const completedJob = await (await request.get(`${API_URL}/evaluations/jobs/${queuedJob.job_id}`)).json();
    expect(completedJob.status).toBe("succeeded");
    expect(completedJob.run_id).toEqual(expect.any(Number));
    runId = completedJob.run_id;
    await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
    await expect(page.getByRole("heading", { name: `Run #${runId}`, exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Results (12)", exact: true })).toBeVisible();
    const run = await (await request.get(`${API_URL}/evaluations/runs/${runId}/summary`)).json();
    expect(run.summary.passed_cases).toBe(8);
    expect(run.config.min_token_f1).toBe(recording.config.min_token_f1);
    expect(run.config.default_max_latency_ms).toBe(recording.config.default_max_latency_ms);
  } finally {
    if (runId !== undefined) {
      const deleted = await request.delete(`${API_URL}/evaluations/runs/${runId}`);
      expect(deleted.status()).toBe(204);
    }
  }
});
