import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { ChatDiagnostics, ChatMessage, ChatTrace, DiagnosticState } from "../api/types";
import { num } from "../lib/format";
import { Badge } from "./ui";

function Field({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <>
      <dt>{label}</dt>
      <dd className="mono">{String(value)}</dd>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="inspector-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

/** Everything captured for one assistant reply. Nothing here is inferred by the UI. */
export function ExecutionInspector({ message, trace, question }: { message: ChatMessage; trace: ChatTrace; question: string }) {
  const { retrieval, prompt, model, settings, generation, live_check: check } = trace;
  return (
    <div className="inspector" aria-label="Execution inspector">
      <Section title="1. Retrieval">
        <dl className="kv">
          <Field label="Query" value={retrieval.query} />
          <Field label="Retriever" value={`${retrieval.retriever}, top-${retrieval.k}`} />
        </dl>
        {retrieval.used_previous_turn && <p className="small muted">Follow-up detected: the previous question was added to the retrieval query.</p>}
        <table className="table compact">
          <thead>
            <tr>
              <th>Passage</th>
              <th>BM25</th>
              <th>Text</th>
            </tr>
          </thead>
          <tbody>
            {retrieval.passages.map((p) => (
              <tr key={p.passage_id}>
                <td className="mono">{p.passage_id}</td>
                <td>{num(p.bm25_score, 2)}</td>
                <td className="small">
                  <strong>{p.title}</strong> {p.text}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="2. Prompt sent to the model">
        <dl className="kv">
          <Field label="Template" value={`${prompt.template_version} + ${prompt.chat_prompt_version}`} />
          <Field label="Hash" value={prompt.hash.slice(0, 16)} />
          <Field
            label="History"
            value={`${prompt.history_exchanges_included} earlier exchange(s)${prompt.history_truncated ? " (older turns dropped)" : ""}`}
          />
        </dl>
        <details>
          <summary className="small">Show exact messages ({prompt.messages.length})</summary>
          {prompt.messages.map((m, i) => (
            <div key={i} className="prompt-message">
              <Badge tone="neutral">{m.role}</Badge>
              <pre>{m.content}</pre>
            </div>
          ))}
        </details>
      </Section>

      <Section title="3. Model and generation">
        <dl className="kv">
          <Field label="Model" value={model.model_id} />
          <Field label="Revision" value={typeof model.model_revision === "string" ? model.model_revision.slice(0, 12) : null} />
          <Field label="Backend" value={model.backend} />
          <Field label="Settings" value={`temperature ${settings.temperature}, max ${settings.max_tokens} tokens, seed ${settings.seed}`} />
          <Field label="Latency" value={generation.latency_ms !== undefined ? `${num(Number(generation.latency_ms), 0)} ms` : null} />
          <Field label="Tokens" value={generation.completion_tokens !== undefined ? `${generation.prompt_tokens} prompt + ${generation.completion_tokens} generated` : null} />
          <Field label="Throughput" value={generation.tokens_per_second !== undefined ? `${generation.tokens_per_second} tok/s` : null} />
          <Field label="Peak memory" value={generation.peak_memory_mb !== undefined ? `${generation.peak_memory_mb} MB` : null} />
          <Field label="Finish" value={generation.finish_reason} />
        </dl>
        <details>
          <summary className="small">Raw model output (unchanged)</summary>
          <pre className="raw-output">{message.content}</pre>
        </details>
        {model.backend === "fixture" && <p className="small muted">Fixture backend: a deterministic stand-in, not a real model.</p>}
      </Section>

      <Section title="4. Live lexical check">
        <p className="lexical-label" data-testid="lexical-label">
          {check.label}
        </p>
        <div className="badges">
          <Badge tone={check.passed_lexical_checks ? "good" : "bad"}>{check.passed_lexical_checks ? "lexical checks passed" : "lexical checks failed"}</Badge>
          {check.groundedness !== null && <Badge tone="neutral">{`lexical groundedness ${num(check.groundedness, 2)}`}</Badge>}
          <Badge tone="neutral">{`citations: ${check.citations.join(", ") || "none"}`}</Badge>
          {check.citation_pairs_total > 0 && (
            <Badge tone={check.citation_pairs_supporting === check.citation_pairs_total ? "neutral" : "warn"}>
              {`cited passage supports its sentence: ${check.citation_pairs_supporting}/${check.citation_pairs_total}`}
            </Badge>
          )}
          <Badge tone="neutral">{check.abstained ? "declined to answer" : "answered"}</Badge>
        </div>
        {check.failures.length > 0 && (
          <ul className="small">
            {check.failures.map((f, i) => (
              <li key={i}>
                <strong>{f.check}</strong>: {f.message}
              </li>
            ))}
          </ul>
        )}
        {check.claims.length > 0 && (
          <table className="table compact">
            <thead>
              <tr>
                <th>Claim</th>
                <th>Lexical support</th>
              </tr>
            </thead>
            <tbody>
              {check.claims.map((c, i) => (
                <tr key={i}>
                  <td>
                    {c.text}
                    <div className="muted small">
                      best passage {c.passage_id ?? "–"} · coverage {num(c.coverage, 2)}
                      {c.missing_numbers.length > 0 && ` · numbers not in passage: ${c.missing_numbers.join(", ")}`}
                    </div>
                  </td>
                  <td>
                    <Badge tone={c.supported ? "good" : "bad"}>{c.supported ? "supported" : "unsupported"}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="small muted">
          Abstention: {check.abstention}. Model judge: {trace.judge.status}. Word overlap can reject a correct paraphrase or accept a contradiction; review
          answers yourself.
        </p>
      </Section>

      {trace.diagnostics && <Diagnostics diagnostics={trace.diagnostics} />}

      <SaveAsTestCase message={message} trace={trace} question={question} />
    </div>
  );
}

const STATES: Record<DiagnosticState, { label: string; tone: "bad" | "warn" | "info" }> = {
  potential_contradiction: { label: "Potential contradiction", tone: "bad" },
  citation_concern: { label: "Citation concern", tone: "warn" },
  needs_human_review: { label: "Needs human review", tone: "info" },
};

function Diagnostics({ diagnostics }: { diagnostics: ChatDiagnostics }) {
  const { warnings } = diagnostics;
  return (
    <Section title="5. Chat diagnostics (heuristic)">
      <p className="lexical-label" data-testid="diagnostics-label">
        {diagnostics.label}
      </p>
      {warnings.length === 0 ? (
        <p className="small muted">
          {diagnostics.note ?? `No warnings for ${diagnostics.claims_checked} claim(s).`} No warning does not mean the answer is
          correct.
        </p>
      ) : (
        <ul className="diagnostics" aria-label="Diagnostic warnings">
          {warnings.map((w, i) => (
            <li key={i} className="diagnostic">
              <div className="badges">
                <Badge tone={STATES[w.state].tone}>{STATES[w.state].label}</Badge>
                <Badge tone="neutral">heuristic</Badge>
              </div>
              <blockquote>{w.claim}</blockquote>
              <p className="small">{w.reason}</p>
              {w.evidence && (
                <p className="small muted">
                  Evidence <span className="mono">{w.evidence.passage_id}</span>: {w.evidence.text}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className="small muted">
        {diagnostics.version}
        {diagnostics.computed_on_read && " · computed from the stored reply (it predates diagnostics)"} · never changes the
        lexical check, runs or release gates.
      </p>
    </Section>
  );
}

function SaveAsTestCase({ message, trace, question }: { message: ChatMessage; trace: ChatTrace; question: string }) {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState(`chat-${message.id}`);
  const [input, setInput] = useState(question);
  const [gold, setGold] = useState<string[]>([]);
  const [decline, setDecline] = useState(false);
  const [savedId, setSavedId] = useState<number | null>(message.saved_test_case_id);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const candidates = trace.retrieval.passages;
  const labelled = decline || gold.length > 0;

  if (savedId) return <p className="small" data-testid="saved-test-case">Saved as test case #{savedId} (human-labelled).</p>;
  if (!open)
    return (
      <button className="btn btn-small" onClick={() => setOpen(true)}>
        Save as test case
      </button>
    );

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const created = await api.saveChatTestCase(message.id, { key, input, relevant_passage_ids: decline ? [] : gold, expect_abstain: decline });
      setSavedId(created.id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form inset" onSubmit={submit} aria-label="Save as test case">
      <p className="small">
        You choose the expected evidence. The model's answer is <strong>not</strong> copied into the test case. Saved to a separate
        "-chat-captures" dataset.
      </p>
      {trace.retrieval.used_previous_turn && <p className="small warn-text">This was a follow-up: rewrite the question so it stands alone.</p>}
      <label>
        Key
        <input value={key} onChange={(e) => setKey(e.target.value)} required pattern="[A-Za-z0-9_.\-]{1,100}" />
      </label>
      <label>
        Question
        <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={2} required />
      </label>
      <fieldset disabled={decline}>
        <legend className="small">Gold passages (retrieved this turn)</legend>
        {candidates.map((p) => (
          <label key={p.passage_id} className="checkbox small">
            <input
              type="checkbox"
              checked={gold.includes(p.passage_id)}
              onChange={(e) => setGold(e.target.checked ? [...gold, p.passage_id] : gold.filter((g) => g !== p.passage_id))}
            />
            {p.passage_id}: {p.title}
          </label>
        ))}
      </fieldset>
      <label className="checkbox small">
        <input type="checkbox" checked={decline} onChange={(e) => setDecline(e.target.checked)} />
        Should decline (the policies don't answer this question)
      </label>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      <div className="row">
        <button className="btn btn-primary btn-small" disabled={busy || !labelled}>
          Save test case
        </button>
        <button type="button" className="btn btn-small" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}
