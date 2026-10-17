import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { App } from "../App";
import type { ChatStatus, ChatTurn } from "../api/types";
import { dataset } from "../test/fixtures";

const status = (over: Partial<ChatStatus> = {}): ChatStatus => ({
  enabled: true, backend: "fixture", model_id: "fixture/echo-top-passage", model_revision: null, loaded: true,
  busy: false, prompt_versions: ["rag-v1", "rag-v5"], max_message_chars: 2000, history_exchanges: 3,
  scope: "local-only", ...over,
});
const ragDataset = { ...dataset, id: 2, name: "kestrel-policy-rag-v1", passage_count: 20 };
const session = { id: 1, dataset_id: 2, dataset_name: "kestrel-policy-rag-v1", title: "Returns question", prompt_version: "rag-v1",
  created_at: "2026-10-14T00:00:00", updated_at: "2026-10-14T00:00:00", message_count: 0 };

const turn: ChatTurn = {
  session: { ...session, title: "Can I return an unused sofa?", message_count: 2 },
  user_message: { id: 10, role: "user", content: "Can I return an unused sofa?", trace: null, saved_test_case_id: null, created_at: "" },
  assistant_message: {
    id: 11, role: "assistant", content: "Most items can be returned within 30 days. [KB-RET-01]", saved_test_case_id: null, created_at: "",
    trace: {
      retrieval: { query: "Can I return an unused sofa?", used_previous_turn: false, retriever: "bm25", k: 3,
        passages: [{ passage_id: "KB-RET-01", title: "Return window", text: "Most items can be returned within 30 days.", bm25_score: 4.21 }] },
      prompt: { template_version: "rag-v1", chat_prompt_version: "chat-v1", hash: "abcdef0123456789abcdef", history_exchanges_included: 0,
        history_truncated: false, messages: [{ role: "system", content: "You are the assistant" }, { role: "user", content: "Policy passages: ..." }] },
      model: { model_id: "fixture/echo-top-passage", backend: "fixture" },
      settings: { temperature: 0, max_tokens: 256, seed: 0 },
      generation: { latency_ms: 3.2 },
      live_check: { label: "Live lexical check (methodology 2.0.0): ... Not verified semantic correctness; not a release-gate result.",
        methodology_version: "2.0.0", passed_lexical_checks: true, failures: [], abstained: false,
        abstention: "not graded (chat has no answerability ground truth)", citations: ["KB-RET-01"], citations_valid: 1,
        citations_nonexistent: [], citations_not_retrieved: [], citation_pairs_total: 1, citation_pairs_supporting: 1, groundedness: 1, unsupported_claims: [], min_groundedness: 0.8,
        claims: [{ text: "Most items can be returned within 30 days.", citations: [], supported: true, passage_id: "KB-RET-01", coverage: 1, missing_numbers: [] }] },
      judge: { status: "not run (optional model judge is off by default)" },
    },
  },
};  // prettier-ignore

type Handler = (init?: RequestInit, url?: URL) => { status?: number; body: unknown } | Promise<{ status?: number; body: unknown }>;
function mockApi(routes: Record<string, Handler | unknown>) {
  const calls: { key: string; search: string; body?: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const key = `${init?.method ?? "GET"} ${url.pathname}`;
    calls.push({ key, search: url.search, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const route = routes[key];
    if (route === undefined) return new Response(JSON.stringify({ error: { code: "not_found", message: key } }), { status: 404 });
    const resolved = typeof route === "function" ? await (route as Handler)(init, url) : route;
    const isEnvelope = typeof resolved === "object" && resolved !== null && "body" in resolved && "status" in resolved;
    const { status: code = 200, body } = isEnvelope ? (resolved as { status: number; body: unknown }) : { body: resolved };
    return new Response(JSON.stringify(body), { status: code });
  }));
  return calls;
}

function open() {
  render(
    <MemoryRouter initialEntries={["/playground"]}>
      <App />
    </MemoryRouter>,
  );
}

const base = (over: Record<string, unknown> = {}) => ({
  "GET /auth/session": { authenticated: true, mode: "development", user: null, csrf_token: null },
  "GET /chat/status": status(), "GET /datasets": [ragDataset],
  "GET /chat/sessions": { items: [session], total: 1, limit: 50, offset: 0 },
  "GET /chat/sessions/1": { ...session, messages: [] }, ...over,
});  // prettier-ignore

async function openSession() {
  open();
  await userEvent.click(await screen.findByRole("button", { name: "Returns question" }));
  return screen.findByRole("textbox", { name: "Message" });
}

describe("Live playground", () => {
  it("pages chat sessions with bounded requests and preserves the active session", async () => {
    const calls = mockApi(base({
      "GET /chat/sessions": (_init?: RequestInit, url?: URL) => {
        const offset = Number(url?.searchParams.get("offset") ?? 0);
        const items = Array.from({ length: Math.min(50, 51 - offset) }, (_, index) => ({
          ...session,
          id: offset + index + 1,
          title: `Session ${offset + index + 1}`,
        }));
        return { items, total: 51, limit: 50, offset };
      },
      "GET /chat/sessions/1": { ...session, messages: [] },
    }));
    open();

    await userEvent.click(await screen.findByRole("button", { name: "Session 1" }));
    expect(await screen.findByText("1–50 of 51")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByRole("button", { name: "Session 51" })).toBeInTheDocument();
    expect(screen.getByText("51–51 of 51")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Returns question" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await screen.findByRole("button", { name: "Session 1" })).toBeInTheDocument();
    expect(calls.filter((call) => call.key === "GET /chat/sessions").every((call) =>
      call.search.includes("limit=50"),
    )).toBe(true);
  });

  it("shows empty and error states for chat session pages", async () => {
    mockApi(base({ "GET /chat/sessions": { items: [], total: 0, limit: 50, offset: 0 } }));
    open();
    expect(await screen.findByText("No chats yet.")).toBeInTheDocument();
    expect(screen.getByText("0–0 of 0")).toBeInTheDocument();

    mockApi(base({
      "GET /chat/sessions": {
        status: 503,
        body: { error: { code: "offline", message: "chat history unavailable" } },
      },
    }));
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent("chat history unavailable");
  });

  it("explains how to enable chat when it is disabled", async () => {
    mockApi(base({ "GET /chat/status": status({ enabled: false, backend: "off", model_id: null }) }));
    const box = await openSession();
    expect(screen.getByTestId("chat-status")).toHaveTextContent("chat disabled");
    expect(screen.getByTestId("chat-status")).toHaveTextContent("Local-only and authenticated");
    expect(box).toBeDisabled();
  });

  it("sends a message and shows the execution inspector with honest labels", async () => {
    const calls = mockApi(base({ "POST /chat/sessions/1/messages": { status: 201, body: turn } }));
    await userEvent.type(await openSession(), "Can I return an unused sofa?{Enter}");
    expect(await screen.findByText("Most items can be returned within 30 days. [KB-RET-01]", { selector: ":not(pre)" })).toBeInTheDocument();
    const inspector = screen.getByLabelText("Execution inspector");
    expect(within(inspector).getByTestId("lexical-label")).toHaveTextContent("Not verified semantic correctness");
    expect(within(inspector).getByText("4.21")).toBeInTheDocument();
    expect(within(inspector).getByText("abcdef0123456789")).toBeInTheDocument();
    expect(within(inspector).getByText(/not graded/)).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /chat/sessions/1/messages")?.body).toEqual({ content: "Can I return an unused sofa?" });
  });

  it("shows a busy error with Retry that resends the same message", async () => {
    let attempts = 0;
    const calls = mockApi(base({
      "POST /chat/sessions/1/messages": () => (++attempts === 1
        ? { status: 429, body: { error: { code: "busy", message: "the model is already generating a reply; try again in a moment" } } }
        : { status: 201, body: turn }),
    }));  // prettier-ignore
    await userEvent.type(await openSession(), "Can I return an unused sofa?{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("already generating");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Most items can be returned within 30 days. [KB-RET-01]", { selector: ":not(pre)" })).toBeInTheDocument();
    const sends = calls.filter((c) => c.key === "POST /chat/sessions/1/messages");
    expect(sends).toHaveLength(2);
    expect(sends[1]?.body).toEqual(sends[0]?.body);
  });

  it("requires human-selected evidence before saving a test case", async () => {
    const calls = mockApi(base({
      "POST /chat/sessions/1/messages": { status: 201, body: turn },
      "POST /chat/messages/11/test-case": { status: 201, body: { id: 77 } },
    }));  // prettier-ignore
    await userEvent.type(await openSession(), "Can I return an unused sofa?{Enter}");
    await userEvent.click(await screen.findByRole("button", { name: "Save as test case" }));
    const form = screen.getByRole("form", { name: "Save as test case" });
    const save = within(form).getByRole("button", { name: "Save test case" });
    expect(save).toBeDisabled();
    expect(within(form).getByRole("checkbox", { name: /KB-RET-01/ })).not.toBeChecked();
    await userEvent.click(within(form).getByRole("checkbox", { name: /KB-RET-01/ }));
    await userEvent.click(save);
    expect(await screen.findByTestId("saved-test-case")).toHaveTextContent("#77");
    expect(calls.find((c) => c.key === "POST /chat/messages/11/test-case")?.body).toEqual({
      key: "chat-11",
      input: "Can I return an unused sofa?",
      relevant_passage_ids: ["KB-RET-01"],
      expect_abstain: false,
    });
  });
});
