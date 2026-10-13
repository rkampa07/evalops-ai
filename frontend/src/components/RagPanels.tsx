import type { ModelGradedSummary, Result, RunSummary } from "../api/types";
import { num, pct } from "../lib/format";
import { Badge, Card, Meter, Stat } from "./ui";

/** Deterministic RAG evidence metrics (computed by EvalOps, reproducible). */
export function RagEvidenceCard({ s }: { s: RunSummary }) {
  return (
    <Card title={<>RAG evidence <Badge tone="info">deterministic</Badge></>}>
      <table className="table compact">
        <tbody>
          <tr><td>Retrieval recall@k</td><td className="w-meter"><Meter value={s.retrieval_recall_at_k ?? null} label="retrieval recall" /></td></tr>
          <tr><td>Citation validity</td><td className="w-meter"><Meter value={s.citation_validity_rate ?? null} label="citation validity" /></td></tr>
          <tr><td>Groundedness (lexical)</td><td className="w-meter"><Meter value={s.groundedness ?? null} label="groundedness" /></td></tr>
          <tr><td>Abstention accuracy</td><td className="w-meter"><Meter value={s.abstention_accuracy ?? null} label="abstention accuracy" /></td></tr>
        </tbody>
      </table>
      <p className="muted small">
        MRR {num(s.mrr ?? null)} · {s.unsupported_claim_count ?? 0} unsupported claim(s) across {s.rag_cases} RAG case(s). Groundedness is a
        lexical heuristic (content-word coverage + exact numbers) against the retrieved passages, not a fact check.
      </p>
    </Card>
  );
}

/** Model-judge aggregates. Always labelled as estimates. */
export function EstimatedCard({ mg }: { mg: ModelGradedSummary }) {
  return (
    <Card className="estimated" title={<>Model-based scores <Badge tone="warn">estimated, not ground truth</Badge></>}>
      <div className="stat-grid">
        <Stat label="Estimated groundedness" value={pct(mg.estimated_groundedness)} hint={`${mg.claims_supported}/${mg.claims_total - mg.claims_error} claims`} />
        <Stat label="Judge vs heuristic agreement" value={pct(mg.heuristic_judge_agreement)} hint={`${mg.claims_error} unparseable verdict(s)`} />
      </div>
      <p className="muted small">
        Judge <code>{mg.judge.model_id}</code> ({mg.judge.backend}, prompt {mg.judge.prompt_version}, revision{" "}
        {mg.judge.model_revision?.slice(0, 10) ?? "n/a"}). Its measured agreement with labelled claims is reported in
        docs/rag-evaluation.md; treat these numbers as a second opinion.
      </p>
    </Card>
  );
}

export function GenerationCard({ generation }: { generation: Record<string, unknown> }) {
  const settings = generation.settings as Record<string, unknown> | undefined;
  const retrieval = generation.retrieval as Record<string, unknown> | undefined;
  const rows: [string, unknown][] = [
    ["Model", generation.model_id],
    ["Revision", typeof generation.model_revision === "string" ? generation.model_revision.slice(0, 12) : null],
    ["Backend", [generation.backend, generation.mlx && `mlx ${generation.mlx}`].filter(Boolean).join(" · ")],
    ["Prompt", `${generation.prompt_version ?? ""} (${String(generation.prompt_hash ?? "").slice(0, 12)})`],
    ["Decoding", settings ? `temperature ${settings.temperature}, max ${settings.max_tokens} tokens, seed ${settings.seed}` : null],
    ["Retrieval", retrieval ? `${retrieval.retriever} top-${retrieval.k}` : null],
    ["Generated", generation.generated_at],
  ];
  return (
    <Card title="Generation metadata">
      <dl className="kv">
        {rows
          .filter(([, v]) => v !== null && v !== undefined && v !== "")
          .map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>{k}</dt>
              <dd className="mono">{String(v)}</dd>
            </div>
          ))}
      </dl>
    </Card>
  );
}

/** Per-claim verdicts: deterministic heuristic next to the (estimated) model judge. */
export function ClaimsTable({ r }: { r: Result }) {
  const claims = r.metrics.claims ?? [];
  if (!r.metrics.rag) return null;
  const judged = r.model_grades?.claims ?? [];
  return (
    <div className="claims">
      <div className="small muted">
        Retrieved: {(r.retrieved_passage_ids ?? []).join(", ") || "none"} · cited: {(r.metrics.citations ?? []).join(", ") || "none"}
        {r.metrics.abstained && " · assistant declined to answer"}
      </div>
      {claims.length > 0 && (
        <table className="table compact">
          <thead>
            <tr>
              <th>Claim</th>
              <th>Heuristic</th>
              <th>Judge (estimated)</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((c, i) => (
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
                <td>
                  {judged[i] ? (
                    <Badge tone={judged[i].verdict === "supported" ? "good" : judged[i].verdict === "error" ? "neutral" : "bad"}>
                      {judged[i].verdict}
                    </Badge>
                  ) : (
                    <span className="muted small">not graded</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
