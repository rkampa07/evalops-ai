import { expect, test, type Page } from "@playwright/test";

/** Select the <option> whose text contains `text` in the select labelled `label`. */
async function pick(page: Page, label: string | RegExp, text: string) {
  const select = page.getByLabel(label);
  const option = select.locator("option", { hasText: text });
  await expect(option).toHaveCount(1);
  await select.selectOption((await option.getAttribute("value"))!);
}

test("dataset viewing: datasets, test cases and knowledge base come from the API", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Datasets" }).click();
  await expect(page.getByRole("heading", { name: "Datasets", exact: true })).toBeVisible();
  const row = page.getByRole("row", { name: /kestrel-support-v1/ });
  await expect(row).toContainText("12");
  await row.getByRole("link", { name: "kestrel-support-v1" }).click();
  await expect(page.getByRole("heading", { name: "kestrel-support-v1" })).toBeVisible();
  await expect(page.getByText("Test cases (12)")).toBeVisible();
  await expect(page.getByRole("button", { name: "safety-unauthorized-compensation" })).toBeVisible();

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Datasets" }).click();
  await expect(page.getByRole("heading", { name: "Datasets", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "kestrel-policy-rag-v1" }).click();
  await expect(page.getByText("Knowledge base (20 passages)")).toBeVisible();
  await page.getByRole("button", { name: /^Show(?: knowledge base passages)?$/ }).click();
  await expect(page.getByRole("cell", { name: "KB-SHP-02", exact: true })).toBeVisible();
  await expect(page.getByText("should decline")).toHaveCount(3);
});

test("baseline vs candidate comparison detects the regression", async ({ page }) => {
  await page.goto("/compare");
  await pick(page, "Baseline run", "v1.3 — Production baseline");
  const response = page.waitForResponse((r) => r.url().includes("/evaluations/compare") && r.status() === 200);
  await pick(page, "Candidate run", "v1.4 — Candidate");
  const body = await (await response).json();
  expect(body.has_regression).toBe(true); // the real API's answer, not a mock
  expect(body.case_counts.regressed).toBe(4);

  await expect(page.getByTestId("compare-verdict")).toHaveText("Regression detected");
  await expect(page.getByText("regressed: 4")).toBeVisible();
  const row = page.getByRole("row", { name: /safety-unauthorized-compensation/ });
  await expect(row).toContainText("regressed");
  await expect(row).toContainText("forbidden terms");
});

test("RAG comparison separates deterministic and estimated metrics", async ({ page }) => {
  await page.goto("/compare");
  await pick(page, "Baseline run", "rag-v1");
  await pick(page, "Candidate run", "rag-v2");
  await expect(page.getByTestId("compare-verdict")).toHaveText("Regression detected");
  await expect(page.getByRole("row", { name: /Groundedness \(lexical\)/ })).toContainText("regressed");
  await expect(page.getByText("estimated, not part of the verdict")).toBeVisible();
  await expect(page.getByRole("row", { name: /q-showroom-hours/ })).toContainText("regressed");
});

test("release readiness report: failing candidate, fixed candidate, markdown export", async ({ page }) => {
  await page.goto("/reports");
  await pick(page, "Candidate run", "v1.4 — Candidate");
  await pick(page, /Baseline run/, "v1.3 — Production baseline");
  await expect(page.getByTestId("verdict")).toHaveText("NOT READY FOR RELEASE");
  await expect(page.getByRole("row", { name: /max_regressed_cases/ })).toContainText("fail");

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download Markdown" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^release-report-run-\d+\.md$/);
  const text = await (await file.createReadStream()).toArray();
  expect(Buffer.concat(text).toString()).toContain("NOT READY FOR RELEASE");

  await pick(page, "Candidate run", "v1.5 — Candidate");
  await pick(page, /Baseline run/, "v1.3 — Production baseline");
  await expect(page.getByTestId("verdict")).toHaveText("READY FOR RELEASE");
});

test("RAG release report applies the predefined RAG gates", async ({ page }) => {
  await page.goto("/reports");
  await pick(page, "Candidate run", "rag-v2");
  await pick(page, /Baseline run/, "rag-v1");
  await expect(page.getByTestId("verdict")).toHaveText("NOT READY FOR RELEASE");
  for (const gate of ["min_groundedness", "min_abstention_accuracy", "max_regressed_cases"]) {
    await expect(page.getByRole("row", { name: new RegExp(gate) })).toContainText("fail");
  }
  await expect(page.getByText(/Model-based metrics are ESTIMATES/)).toBeVisible();
});
