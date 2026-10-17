import { expect, test } from "@playwright/test";
import { API_URL } from "./environment";

// Real API + UI; the API runs live chat with the deterministic fixture backend (no model).
test("live playground: chat, follow-up, execution inspector, save as test case", async ({ page, request }) => {
  const runsBefore = (await (await request.get(`${API_URL}/evaluations/runs`)).json()).total;
  const datasets = await (await request.get(`${API_URL}/datasets`)).json();
  const source = datasets.find((d: { name: string }) => d.name === "kestrel-policy-rag-v1");
  const versionBefore = source.version;

  await page.goto("/playground");
  await expect(page.getByTestId("chat-status")).toContainText("backend: fixture");
  await expect(page.getByTestId("chat-status")).toContainText("Local-only and authenticated");
  await page.getByLabel("Knowledge base").selectOption({ label: "kestrel-policy-rag-v1 (20 passages)" });
  await page.getByRole("button", { name: "New chat" }).click();

  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill("How long do I have to return an item?");
  const first = page.waitForResponse((r) => r.url().includes("/messages") && r.request().method() === "POST");
  await box.press("Enter");
  const turn = await (await first).json();
  expect(turn.assistant_message.trace.live_check.methodology_version).toBe("2.0.0");
  await expect(page.getByText(/Most items can be returned within 30 days/).first()).toBeVisible();

  const inspector = page.getByLabel("Execution inspector");
  await expect(inspector.getByTestId("lexical-label")).toContainText("Not verified semantic correctness");
  await expect(inspector.getByRole("cell", { name: "KB-RET-01", exact: true })).toBeVisible();
  await inspector.getByText(/Show exact messages/).click();
  await expect(inspector.getByText("system", { exact: true })).toBeVisible();

  // Ambiguous follow-up uses the previous question for retrieval.
  await box.fill("What about Canada?");
  await box.press("Enter");
  await expect(page.getByText("Follow-up detected")).toBeVisible();

  // Save the first reply as a human-labelled test case.
  await page.getByRole("button", { name: "Inspect" }).first().click();
  await page.getByRole("button", { name: "Save as test case" }).first().click();
  const form = page.getByRole("form", { name: "Save as test case" });
  await expect(form.getByRole("button", { name: "Save test case" })).toBeDisabled();
  await form.getByRole("checkbox", { name: /KB-RET-01/ }).check();
  await form.getByRole("button", { name: "Save test case" }).click();
  await expect(page.getByTestId("saved-test-case")).toContainText("Saved as test case");

  // Chat never creates evaluation runs or changes the source dataset.
  expect((await (await request.get(`${API_URL}/evaluations/runs`)).json()).total).toBe(runsBefore);
  const after = await (await request.get(`${API_URL}/datasets/${source.id}`)).json();
  expect(after.version).toBe(versionBefore);

  // Clean up so other browser tests see the seeded state only.
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete chat" }).click();
  await expect(page.getByText("No chat selected")).toBeVisible();
  const captures = (await (await request.get(`${API_URL}/datasets`)).json()).find(
    (d: { name: string }) => d.name === "kestrel-policy-rag-v1-chat-captures",
  );
  expect(captures).toBeTruthy();
  expect((await request.delete(`${API_URL}/datasets/${captures.id}`)).status()).toBe(204);
});
