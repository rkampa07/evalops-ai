import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api } from "../api/client";
import type { ChatMessage, ChatSessionDetail } from "../api/types";
import { ExecutionInspector } from "../components/ExecutionInspector";
import {
  Badge,
  Card,
  Empty,
  ErrorBanner,
  Loading,
  PageHeader,
  PaginationControls,
} from "../components/ui";
import { useAsync } from "../lib/useAsync";

const PAGE_SIZE = 50;

export function Playground() {
  const status = useAsync(() => api.chatStatus(), []);
  const datasets = useAsync(() => api.listDatasets(), []);
  const [sessionOffset, setSessionOffset] = useState(0);
  const sessions = useAsync(
    () => api.listChatSessionsPage({ limit: PAGE_SIZE, offset: sessionOffset }),
    [sessionOffset],
  );
  const [active, setActive] = useState<ChatSessionDetail>();
  const [datasetId, setDatasetId] = useState("");
  const [promptVersion, setPromptVersion] = useState("rag-v1");
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string>();
  const [error, setError] = useState<{ message: string; retryText?: string }>();
  const [inspected, setInspected] = useState<number>();
  const bottom = useRef<HTMLDivElement>(null);

  const ragDatasets = (datasets.data ?? []).filter((d) => d.passage_count > 0);
  useEffect(() => {
    if (!datasetId && ragDatasets[0]) setDatasetId(String(ragDatasets[0].id));
  }, [datasetId, ragDatasets]);
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "end" });
  }, [active?.messages.length, pending]);
  useEffect(() => {
    if (sessions.data && sessionOffset > 0 && sessionOffset >= sessions.data.total) {
      setSessionOffset(Math.max(0, (Math.ceil(sessions.data.total / PAGE_SIZE) - 1) * PAGE_SIZE));
    }
  }, [sessionOffset, sessions.data]);

  function refreshNewestSessions() {
    if (sessionOffset === 0) sessions.reload();
    else setSessionOffset(0);
  }

  async function openSession(id: number) {
    setError(undefined);
    setInspected(undefined);
    try {
      setActive(await api.getChatSession(id));
    } catch (err) {
      setError({ message: (err as Error).message });
    }
  }

  async function newSession() {
    setError(undefined);
    try {
      const created = await api.createChatSession({ dataset_id: Number(datasetId), prompt_version: promptVersion });
      refreshNewestSessions();
      await openSession(created.id);
    } catch (err) {
      setError({ message: (err as Error).message });
    }
  }

  async function deleteSession() {
    if (!active || !window.confirm("Delete this chat and its traces?")) return;
    try {
      await api.deleteChatSession(active.id);
      setActive(undefined);
      sessions.reload();
    } catch (err) {
      setError({ message: (err as Error).message });
    }
  }

  async function send(text: string) {
    if (!active || !text.trim() || pending) return;
    setPending(text);
    setError(undefined);
    setDraft("");
    try {
      const turn = await api.sendChatMessage(active.id, text);
      setActive({ ...active, ...turn.session, messages: [...active.messages, turn.user_message, turn.assistant_message] });
      setInspected(turn.assistant_message.id);
      refreshNewestSessions();
    } catch (err) {
      setError({ message: (err as Error).message, retryText: text });
      setDraft(text);
    } finally {
      setPending(undefined);
      status.reload();
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void send(draft);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(draft);
    }
  }

  const questionFor = (messages: ChatMessage[], index: number) => messages[index - 1]?.content ?? "";
  const s = status.data;

  return (
    <>
      <PageHeader
        title="Live playground"
        subtitle="Chat with the policy assistant and inspect every step: retrieval, the exact prompt, the model and the live lexical check."
      />
      {status.error ? (
        <ErrorBanner error={status.error} onRetry={status.reload} />
      ) : s ? (
        <div className={`status-banner ${s.enabled ? "" : "status-off"}`} data-testid="chat-status">
          <Badge tone={s.enabled ? "good" : "warn"}>{s.enabled ? `backend: ${s.backend}` : "chat disabled"}</Badge>
          {s.enabled ? (
            <span className="small">
              {s.model_id} {s.backend === "mlx" && (s.loaded ? "(loaded)" : "(loads on first message)")} · history: last {s.history_exchanges} exchanges ·
              max {s.max_message_chars} characters
            </span>
          ) : (
            <span className="small">
              Start the API with <code>EVALOPS_CHAT_BACKEND=fixture</code> (no model) or <code>mlx</code> (local Qwen) to chat.
            </span>
          )}
          <span className="small muted">Local-only and authenticated. Chat checks never affect runs or release gates.</span>
        </div>
      ) : null}

      <div className="playground">
        <Card title="Chats" className="chat-sessions">
          <div className="form">
            <label>
              Knowledge base
              <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)} aria-label="Knowledge base">
                {ragDatasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} ({d.passage_count} passages)
                  </option>
                ))}
              </select>
            </label>
            <label>
              Prompt
              <select value={promptVersion} onChange={(e) => setPromptVersion(e.target.value)} aria-label="Prompt version">
                {(s?.prompt_versions ?? ["rag-v1"]).map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
            <button className="btn btn-primary" onClick={newSession} disabled={!datasetId}>
              New chat
            </button>
          </div>
          {sessions.loading ? (
            <Loading />
          ) : sessions.error ? (
            <ErrorBanner error={sessions.error} onRetry={sessions.reload} />
          ) : !sessions.data?.total ? (
            <Empty>No chats yet.</Empty>
          ) : (
            <ul className="session-list">
              {sessions.data.items.map((c) => (
                <li key={c.id}>
                  <button className={`link ${active?.id === c.id ? "active" : ""}`} onClick={() => openSession(c.id)}>
                    {c.title}
                  </button>
                  <div className="muted small">
                    {c.prompt_version} · {c.message_count} messages
                  </div>
                </li>
              ))}
            </ul>
          )}
          {sessions.data && (
            <PaginationControls
              limit={sessions.data.limit}
              offset={sessionOffset}
              total={sessions.data.total}
              loading={sessions.loading}
              onOffsetChange={setSessionOffset}
            />
          )}
        </Card>

        <Card
          className="chat-main"
          title={active ? active.title : "No chat selected"}
          actions={
            active && (
              <button className="btn btn-small btn-danger" onClick={deleteSession}>
                Delete chat
              </button>
            )
          }
        >
          {!active ? (
            <Empty>Choose a knowledge base and prompt, then start a new chat.</Empty>
          ) : (
            <>
              <div className="messages" aria-live="polite">
                {active.messages.length === 0 && !pending && <Empty>Ask a customer-support question, e.g. "Can I return an unused sofa?"</Empty>}
                {active.messages.map((m, i) => (
                  <div key={m.id} className={`bubble bubble-${m.role}`}>
                    <div className="bubble-text">{m.content}</div>
                    {m.role === "assistant" && m.trace && (
                      <div className="bubble-meta">
                        <Badge tone={m.trace.live_check.passed_lexical_checks ? "good" : "bad"}>
                          {m.trace.live_check.passed_lexical_checks ? "lexical checks passed" : "lexical checks failed"}
                        </Badge>
                        <span className="muted small">{Math.round(Number(m.trace.generation.latency_ms ?? 0))} ms</span>
                        <button
                          className="btn btn-small"
                          aria-expanded={inspected === m.id}
                          onClick={() => setInspected(inspected === m.id ? undefined : m.id)}
                        >
                          {inspected === m.id ? "Hide inspector" : "Inspect"}
                        </button>
                      </div>
                    )}
                    {inspected === m.id && m.trace && <ExecutionInspector message={m} trace={m.trace} question={questionFor(active.messages, i)} />}
                  </div>
                ))}
                {pending && (
                  <>
                    <div className="bubble bubble-user">
                      <div className="bubble-text">{pending}</div>
                    </div>
                    <div className="bubble bubble-assistant" role="status">
                      <div className="bubble-text muted">Generating…</div>
                    </div>
                  </>
                )}
                <div ref={bottom} />
              </div>
              {error && (
                <div className="error-banner" role="alert">
                  <span>{error.message}</span>
                  {error.retryText && (
                    <button className="btn btn-small" onClick={() => void send(error.retryText!)}>
                      Retry
                    </button>
                  )}
                </div>
              )}
              <form className="composer" onSubmit={onSubmit}>
                <textarea
                  aria-label="Message"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={onKeyDown}
                  rows={2}
                  maxLength={s?.max_message_chars ?? 2000}
                  placeholder={s?.enabled ? "Type a question (Enter to send, Shift+Enter for a new line)" : "Chat is disabled"}
                  disabled={!s?.enabled || !!pending}
                />
                <button className="btn btn-primary" disabled={!s?.enabled || !!pending || !draft.trim()}>
                  {pending ? "Generating…" : "Send"}
                </button>
              </form>
            </>
          )}
          {!active && error && <ErrorBanner error={error.message} />}
        </Card>
      </div>
    </>
  );
}
