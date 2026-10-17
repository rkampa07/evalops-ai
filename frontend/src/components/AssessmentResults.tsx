import type {
  AssessmentResult,
  CitationAssessmentResult,
  CitationDecision,
  ClaimAssessmentResult,
  ClaimVerdict,
} from "../api/types";
import { num } from "../lib/format";
import { Badge, Card } from "./ui";

type BadgeTone = "good" | "bad" | "warn" | "neutral" | "info";

const CITATION_STATUS_TONES: Record<string, BadgeTone> = {
  correct: "good",
  partial: "warn",
  missing_citation: "warn",
  not_retrieved: "warn",
  unresolved: "warn",
  wrong_passage: "bad",
  unrelated: "bad",
  invalid_citation: "bad",
  not_applicable: "neutral",
};

const CLAIM_LABEL_TONES: Record<string, BadgeTone> = {
  supported: "good",
  contradicted: "bad",
  insufficient_evidence: "warn",
  needs_human_review: "info",
};

const CLAIM_RESULT_STATUS_TONES: Record<string, BadgeTone> = {
  assessed: "info",
  no_checkable_claims: "neutral",
};

function shortHash(value: string | null | undefined, length = 12): string | null {
  if (typeof value !== "string" || value.length === 0) return null;
  return value.slice(0, length);
}

function citationTone(status: string | null | undefined): BadgeTone {
  if (!status) return "neutral";
  return CITATION_STATUS_TONES[status] ?? "neutral";
}

function claimLabelTone(label: string | null | undefined): BadgeTone {
  if (!label) return "neutral";
  return CLAIM_LABEL_TONES[label] ?? "neutral";
}

function claimStatusTone(status: string | null | undefined): BadgeTone {
  if (!status) return "neutral";
  return CLAIM_RESULT_STATUS_TONES[status] ?? "neutral";
}

export function isCitationAssessmentResult(
  result: AssessmentResult,
): result is CitationAssessmentResult {
  return "per_citation" in result && Array.isArray(result.per_citation);
}

export function isClaimAssessmentResult(
  result: AssessmentResult,
): result is ClaimAssessmentResult {
  return "claims" in result && Array.isArray(result.claims);
}

function formatCoverage(values: number[] | null | undefined): string | null {
  if (!Array.isArray(values) || values.length === 0) return null;
  const formatted = values
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v))
    .map((v) => num(v, 2));
  return formatted.length > 0 ? formatted.join(", ") : null;
}

function formatDecidingEvidenceItem(item: Record<string, unknown>, index: number) {
  const passageId = typeof item.passage_id === "string" && item.passage_id ? item.passage_id : null;
  const relation = typeof item.relation === "string" && item.relation ? item.relation : null;
  const scope = typeof item.scope === "string" && item.scope ? item.scope : null;
  const unit = typeof item.unit === "string" && item.unit ? item.unit : null;
  const details = [relation, scope, unit].filter(Boolean).join(" · ");

  if (!passageId && !details) return null;
  return (
    <li key={`${passageId ?? "evidence"}-${index}`}>
      {passageId && <span className="mono">{passageId}</span>}
      {passageId && details ? ": " : ""}
      {details && <span>{details}</span>}
    </li>
  );
}

function CitationDecisionRow({ decision }: { decision: CitationDecision }) {
  const coverage = formatCoverage(decision.component_coverage);
  const status = decision.status || "unknown";
  return (
    <tr>
      <td className="mono">{decision.citation_id ?? "none"}</td>
      <td>
        <Badge tone={citationTone(status)}>{status}</Badge>
      </td>
      <td style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>
        <div>{decision.reason || "—"}</div>
        <div className="muted small">
          {decision.reason_code && <span className="mono">{decision.reason_code}</span>}
          {decision.reason_code && coverage ? " · " : ""}
          {coverage && `component coverage: ${coverage}`}
        </div>
      </td>
    </tr>
  );
}

/** Read-only presentation for a completed frozen citation assessment. */
export function CitationAssessmentResultCard({ result }: { result: CitationAssessmentResult }) {
  const decisions = Array.isArray(result.per_citation) ? result.per_citation : [];
  const status = result.status || "unknown";
  const kbHash = shortHash(result.knowledge_base_hash);
  const configHash = shortHash(result.config_digest);
  const inputHash = shortHash(result.input_fingerprint);

  return (
    <Card
      title={
        <>
          Citation assessment <Badge tone="info">frozen evaluator</Badge>
        </>
      }
      actions={<Badge tone={citationTone(status)}>{status}</Badge>}
    >
      <p className="muted small" data-testid="citation-assessment-disclaimer">
        Frozen citation assessment (<code>{result.evaluator_version}</code>) — separate from deterministic lexical run metrics and release gates.
      </p>
      <dl className="kv small">
        <dt>Assessment</dt>
        <dd className="mono">
          #{result.assessment_id} · case {result.case_key}
        </dd>
        <dt>Outcome</dt>
        <dd style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>
          {result.reason || "—"}
          {result.reason_code && (
            <>
              {" "}
              <span className="muted mono">({result.reason_code})</span>
            </>
          )}
        </dd>
        <dt>Provenance</dt>
        <dd className="mono">
          {[
            configHash && `config ${configHash}`,
            inputHash && `input ${inputHash}`,
            kbHash ? `kb ${kbHash}` : "kb n/a",
          ]
            .filter(Boolean)
            .join(" · ")}
        </dd>
      </dl>

      {decisions.length === 0 ? (
        <p className="muted small">No individual citations recorded for this response.</p>
      ) : (
        <table className="table compact" aria-label="Citation decisions">
          <thead>
            <tr>
              <th>Citation</th>
              <th>Status</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {decisions.map((decision, i) => (
              <CitationDecisionRow key={`${decision.citation_id ?? "none"}-${i}`} decision={decision} />
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function ClaimVerdictRow({ verdict }: { verdict: ClaimVerdict }) {
  const label = verdict.label || "unknown";
  const guards = Array.isArray(verdict.guards) ? verdict.guards.filter(Boolean) : [];
  const evidenceItems = Array.isArray(verdict.deciding_evidence)
    ? verdict.deciding_evidence
        .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
        .map((item, idx) => formatDecidingEvidenceItem(item, idx))
        .filter(Boolean)
    : [];

  return (
    <tr>
      <td style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>
        <div>{verdict.claim_text || "—"}</div>
        <div className="muted small">
          branch <span className="mono">{verdict.diagnostic_branch || "unknown"}</span>
          {guards.length > 0 && ` · guards: ${guards.join(", ")}`}
        </div>
      </td>
      <td>
        <div className="badges">
          <Badge tone={claimLabelTone(label)}>{label}</Badge>
          {verdict.abstained && <Badge tone="neutral">abstained</Badge>}
        </div>
      </td>
      <td style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>
        <div>{verdict.reason || "—"}</div>
        {evidenceItems.length > 0 && (
          <ul className="issues muted small" aria-label="Deciding evidence">
            {evidenceItems}
          </ul>
        )}
      </td>
    </tr>
  );
}

interface SimulatedVerifierInfo {
  executedLabel: string;
}

function isSimulatedToken(value: string): boolean {
  const lower = value.toLowerCase();
  return (
    lower.includes("simulated") ||
    lower.includes("demo_stub") ||
    lower.includes("demo-stub") ||
    lower.includes("fixture")
  );
}

function detectSimulatedClaimVerifier(claims: ClaimVerdict[]): SimulatedVerifierInfo | null {
  for (const verdict of claims) {
    if (!verdict || typeof verdict !== "object") continue;
    const model =
      verdict.model && typeof verdict.model === "object" ? verdict.model : undefined;
    const config =
      verdict.config && typeof verdict.config === "object" ? verdict.config : undefined;
    const backend = typeof model?.backend === "string" ? model.backend.trim() : "";
    const modelId = typeof model?.model_id === "string" ? model.model_id.trim() : "";
    const branch =
      typeof verdict.diagnostic_branch === "string" ? verdict.diagnostic_branch.trim() : "";
    const guards = Array.isArray(verdict.guards)
      ? verdict.guards.filter((g): g is string => typeof g === "string")
      : [];

    const hasSimulatedBackend = Boolean(backend && backend !== "transformers");
    const hasSimulatedConfig = config?.simulated_demo === true;
    const hasSimulatedBranch = isSimulatedToken(branch);
    const matchingGuard = guards.find((g) => isSimulatedToken(g));

    if (hasSimulatedBackend || hasSimulatedConfig || hasSimulatedBranch || matchingGuard) {
      const parts: string[] = [];
      if (modelId) parts.push(modelId);
      if (backend && backend !== modelId) parts.push(backend);
      if (parts.length === 0) {
        parts.push(matchingGuard ?? (branch || "simulated_stub"));
      }
      return {
        executedLabel: `${parts.join(" · ")} (simulated — no NLI weights loaded)`,
      };
    }
  }
  return null;
}

/** Read-only presentation for a completed frozen semantic claim assessment. */
export function ClaimAssessmentResultCard({ result }: { result: ClaimAssessmentResult }) {
  const claims = Array.isArray(result.claims) ? result.claims : [];
  const status = result.status || "unknown";
  const revision = shortHash(result.model_revision);
  const configHash = shortHash(result.config_digest);
  const inputHash = shortHash(result.input_fingerprint);
  const kbHash = shortHash(result.knowledge_base_hash);
  const simulatedInfo = detectSimulatedClaimVerifier(claims);

  return (
    <Card
      title={
        <>
          Claim assessment <Badge tone="info">frozen evaluator</Badge>{" "}
          {simulatedInfo && (
            <Badge tone="neutral">Simulated verifier — no NLI model loaded</Badge>
          )}
        </>
      }
      actions={<Badge tone={claimStatusTone(status)}>{status}</Badge>}
    >
      <p className="muted small" data-testid="claim-assessment-disclaimer">
        Frozen semantic claim assessment (<code>{result.evaluator_version}</code>) — separate from deterministic lexical run metrics and release gates.
      </p>
      <dl className="kv small">
        <dt>Assessment</dt>
        <dd className="mono">
          #{result.assessment_id} · case {result.case_key}
        </dd>
        <dt>{simulatedInfo ? "Configured model" : "Model"}</dt>
        <dd className="mono">
          {result.model_id || "unknown"}
          {revision && ` (${revision})`}
        </dd>
        {simulatedInfo && (
          <>
            <dt>Executed verifier</dt>
            <dd className="mono" data-testid="executed-verifier">
              {simulatedInfo.executedLabel}
            </dd>
          </>
        )}
        <dt>Provenance</dt>
        <dd className="mono">
          {[
            configHash && `config ${configHash}`,
            inputHash && `input ${inputHash}`,
            kbHash ? `kb ${kbHash}` : "kb n/a",
          ]
            .filter(Boolean)
            .join(" · ")}
        </dd>
      </dl>

      {claims.length === 0 ? (
        <p className="muted small">No checkable claims extracted from this response.</p>
      ) : (
        <table className="table compact" aria-label="Claim verdicts">
          <thead>
            <tr>
              <th>Claim</th>
              <th>Verdict</th>
              <th>Reason &amp; evidence</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((verdict, i) => (
              <ClaimVerdictRow key={i} verdict={verdict} />
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

/** Structural-narrowing dispatcher for either assessment result contract. */
export function AssessmentResultCard({ result }: { result: AssessmentResult }) {
  if (isCitationAssessmentResult(result)) {
    return <CitationAssessmentResultCard result={result} />;
  }
  return <ClaimAssessmentResultCard result={result} />;
}

export { AssessmentResultCard as AssessmentResults };
