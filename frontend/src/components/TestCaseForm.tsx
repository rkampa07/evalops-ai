import { useState, type FormEvent } from "react";
import type { TestCase, TestCaseInput } from "../api/types";

const splitList = (s: string) =>
  s
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);

export interface TestCaseFormProps {
  initial?: TestCase;
  submitLabel: string;
  onSubmit: (value: TestCaseInput) => Promise<void>;
  onCancel?: () => void;
}

/** Create/edit form. Client checks are convenience only; the API is the source of truth. */
export function TestCaseForm({ initial, submitLabel, onSubmit, onCancel }: TestCaseFormProps) {
  const [key, setKey] = useState(initial?.key ?? "");
  const [input, setInput] = useState(initial?.input ?? "");
  const [expected, setExpected] = useState(initial?.expected_output ?? "");
  const [required, setRequired] = useState(initial?.required_terms.join(", ") ?? "");
  const [forbidden, setForbidden] = useState(initial?.forbidden_terms.join(", ") ?? "");
  const [tags, setTags] = useState(initial?.tags.join(", ") ?? "");
  const [latency, setLatency] = useState(initial?.max_latency_ms?.toString() ?? "");
  const [schema, setSchema] = useState(initial?.json_schema ? JSON.stringify(initial.json_schema, null, 2) : "");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(undefined);
    let jsonSchema: Record<string, unknown> | null = null;
    if (schema.trim()) {
      try {
        jsonSchema = JSON.parse(schema);
      } catch {
        setError("JSON schema is not valid JSON.");
        return;
      }
    }
    const latencyValue = latency.trim() ? Number(latency) : null;
    if (latencyValue !== null && (!Number.isFinite(latencyValue) || latencyValue <= 0)) {
      setError("Max latency must be a positive number.");
      return;
    }
    setBusy(true);
    try {
      await onSubmit({
        key: key.trim(),
        input,
        expected_output: expected.trim() ? expected : null,
        required_terms: splitList(required),
        forbidden_terms: splitList(forbidden),
        tags: splitList(tags),
        max_latency_ms: latencyValue,
        json_schema: jsonSchema,
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={submit} aria-label={submitLabel}>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      <div className="grid-2">
        <label>
          Key
          <input value={key} onChange={(e) => setKey(e.target.value)} required pattern="[A-Za-z0-9_.\-]{1,100}" title="letters, digits, _ . -" />
        </label>
        <label>
          Tags <span className="muted small">(comma separated)</span>
          <input value={tags} onChange={(e) => setTags(e.target.value)} />
        </label>
      </div>
      <label>
        Input / prompt
        <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={2} required />
      </label>
      <label>
        Expected output <span className="muted small">(reference for exact match &amp; token F1)</span>
        <textarea value={expected} onChange={(e) => setExpected(e.target.value)} rows={2} />
      </label>
      <div className="grid-3">
        <label>
          Required terms
          <input value={required} onChange={(e) => setRequired(e.target.value)} placeholder="30 days, refund" />
        </label>
        <label>
          Forbidden terms
          <input value={forbidden} onChange={(e) => setForbidden(e.target.value)} placeholder="guarantee" />
        </label>
        <label>
          Max latency (ms)
          <input value={latency} onChange={(e) => setLatency(e.target.value)} inputMode="numeric" />
        </label>
      </div>
      <label>
        JSON schema <span className="muted small">(optional; output must be JSON matching it)</span>
        <textarea className="mono" value={schema} onChange={(e) => setSchema(e.target.value)} rows={3} placeholder='{"type": "object", "required": ["intent"]}' />
      </label>
      <div className="row">
        <button className="btn btn-primary" disabled={busy}>
          {submitLabel}
        </button>
        {onCancel && (
          <button type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
