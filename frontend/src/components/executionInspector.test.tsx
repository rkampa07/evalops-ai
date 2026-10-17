import { render, screen, within } from "@testing-library/react";
import type { ChatMessage, ChatTrace } from "../api/types";
import { ExecutionInspector } from "./ExecutionInspector";

const output = "Yes, custom-made furniture can be returned [KB-RET-02].";
const trace: ChatTrace = {
  retrieval: { query: "Can I return a custom-made sofa?", used_previous_turn: false, retriever: "bm25", k: 3,
    passages: [{ passage_id: "KB-RET-02", title: "Non-returnable items", text: "Custom-made furniture cannot be returned.", bm25_score: 6.76 }] },
  prompt: { template_version: "rag-v1", chat_prompt_version: "chat-v2", hash: "abcdef0123456789", history_exchanges_included: 0,
    history_truncated: false, messages: [] },
  model: { model_id: "mlx-community/Qwen2.5-1.5B-Instruct-4bit", backend: "mlx" },
  settings: { temperature: 0, max_tokens: 256, seed: 0 },
  generation: { latency_ms: 900 },
  live_check: { label: "Live lexical check … Not verified semantic correctness; not a release-gate result.", methodology_version: "2.0.0",
    passed_lexical_checks: true, failures: [], abstained: false, abstention: "not graded", citations: ["KB-RET-02"], citations_valid: 1,
    citations_nonexistent: [], citations_not_retrieved: [], citation_pairs_total: 1, citation_pairs_supporting: 1, claims: [],
    groundedness: 1, unsupported_claims: [], min_groundedness: 0.8 },
  diagnostics: { version: "chat-diagnostics-v1", label: "Chat diagnostics (heuristic): … Not verified semantic correctness.",
    claims_checked: 1, warnings: [{ state: "potential_contradiction", kind: "policy_polarity", heuristic: true,
      claim: "Yes, custom-made furniture can be returned.", reason: "The answer says this is allowed (return or refund); the passage says it is not allowed.",
      evidence: { passage_id: "KB-RET-02", text: "Custom-made furniture cannot be returned." } }] },
  judge: { status: "not run" },
};  // prettier-ignore
const message: ChatMessage = { id: 7, role: "assistant", content: output, trace, saved_test_case_id: null, created_at: "" };

describe("ExecutionInspector diagnostics", () => {
  it("shows heuristic warnings separately from a passing lexical check, with claim, evidence and reason", () => {
    render(<ExecutionInspector message={message} trace={trace} question="Can I return a custom-made sofa?" />);
    expect(screen.getByText("lexical checks passed")).toBeInTheDocument();
    expect(screen.getByTestId("diagnostics-label")).toHaveTextContent("Not verified semantic correctness");
    const warning = within(screen.getByRole("list", { name: "Diagnostic warnings" })).getByRole("listitem");
    expect(within(warning).getByText("Potential contradiction")).toBeInTheDocument();
    expect(within(warning).getByText("heuristic")).toBeInTheDocument();
    expect(within(warning).getByText("Yes, custom-made furniture can be returned.")).toBeInTheDocument();
    expect(within(warning).getByText("KB-RET-02")).toBeInTheDocument();
    expect(within(warning).getByText(/the passage says it is not allowed/)).toBeInTheDocument();
    expect(screen.getByText(output, { selector: "pre" })).toBeInTheDocument(); // raw output, unchanged
  });

  it("says that no warning is not proof of correctness", () => {
    const clean = { ...trace, diagnostics: { ...trace.diagnostics!, warnings: [] } };
    render(<ExecutionInspector message={{ ...message, trace: clean }} trace={clean} question="q" />);
    expect(screen.getByText(/No warnings for 1 claim\(s\)\. No warning does not mean the answer is correct\./)).toBeInTheDocument();
  });
});
