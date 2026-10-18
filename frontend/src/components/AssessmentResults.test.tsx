import { render, screen, within } from "@testing-library/react";
import type { CitationAssessmentResult, ClaimAssessmentResult } from "../api/types";
import {
  AssessmentResultCard,
  CitationAssessmentResultCard,
  ClaimAssessmentResultCard,
  isCitationAssessmentResult,
  isClaimAssessmentResult,
} from "./AssessmentResults";

const citationFixture: CitationAssessmentResult = {
  assessment_id: 21,
  run_id: 4,
  result_id: 19,
  case_key: "refund-policy-01",
  evaluator_version: "citation-evaluator-v4",
  predictor_sha256: "33ee2d9f39258b5c7f8f6c53fa82b95730a880e500de819cb7aa262d0c51d84b",
  runner_sha256: "99aa11bb22cc33dd44ee55ff6600778899aa11bb22cc33dd44ee55ff66007788",
  config_digest: "cfg0123456789abcdef0123456789abcdef",
  input_fingerprint: "inp9876543210fedcba9876543210fedcba",
  knowledge_base_hash: "kb11223344556677889900aabbccddeeff",
  status: "partial",
  reason_code: "mixed_citation_outcomes",
  reason: "One citation matched retrieved evidence and one cited passage was not retrieved.",
  per_citation: [
    {
      citation_id: "KB-RET-01",
      status: "correct",
      reason_code: "supported_by_cited_passage",
      reason: "Passage directly covers the stated 30-day window.",
      component_coverage: [1, 0.85],
    },
    {
      citation_id: "KB-RET-99",
      status: "not_retrieved",
      reason_code: "cited_passage_not_in_retrieval",
      reason: "Passage was not in the recorded retrieval context.",
      component_coverage: [],
    },
  ],
  created_at: "2026-10-17T00:00:00Z",
};

const claimFixture: ClaimAssessmentResult = {
  assessment_id: 22,
  run_id: 4,
  result_id: 19,
  case_key: "refund-policy-01",
  evaluator_version: "semantic-aggregate-v12",
  source_sha256: "src0123456789abcdef0123456789abcdef",
  config_digest: "cfgfedcba9876543210fedcba9876543210",
  model_id: "cross-encoder/nli-deberta-v3-small",
  model_revision: "fa2804872c3b4bd748f38c0185cc85775361e735",
  input_fingerprint: "inp1234567890abcdef1234567890abcdef",
  knowledge_base_hash: "kb11223344556677889900aabbccddeeff",
  status: "assessed",
  claims: [
    {
      claim_text: "Standard items can be returned within 30 days.",
      label: "supported",
      abstained: false,
      reason: "Entailed by retrieved return policy.",
      diagnostic_branch: "evidence_only",
      deciding_evidence: [
        { passage_id: "KB-RET-01", relation: "entails", scope: "sentence", unit: "s1" },
      ],
      guards: [],
      config: {},
      model: {},
      uncalibrated_scores: [],
      ignored_contradictions: [],
    },
    {
      claim_text: "Custom-made furniture is eligible for free exchanges.",
      label: "needs_human_review",
      abstained: true,
      reason: "Guard triggered due to numeric/exception ambiguity.",
      diagnostic_branch: "exception_guard+numeric_guard",
      deciding_evidence: [{ passage_id: "KB-RET-02", relation: "contradicts" }],
      guards: ["exception_guard", "numeric_guard"],
      config: {},
      model: {},
      uncalibrated_scores: [],
      ignored_contradictions: [],
    },
  ],
  created_at: "2026-10-17T00:00:00Z",
};

describe("AssessmentResults components", () => {
  it("narrows CitationAssessmentResult and ClaimAssessmentResult via structural fields", () => {
    expect(isCitationAssessmentResult(citationFixture)).toBe(true);
    expect(isClaimAssessmentResult(citationFixture)).toBe(false);
    expect(isClaimAssessmentResult(claimFixture)).toBe(true);
    expect(isCitationAssessmentResult(claimFixture)).toBe(false);
  });

  it("renders citation assessment decisions, coverage, provenance, and lexical distinction", () => {
    render(<AssessmentResultCard result={citationFixture} />);

    expect(screen.getByTestId("citation-assessment-disclaimer")).toHaveTextContent(
      /Frozen citation assessment.*citation-evaluator-v4.*separate from deterministic lexical run metrics/,
    );
    expect(screen.getByText(/#21 · case refund-policy-01/)).toBeInTheDocument();
    expect(screen.getByText("partial")).toBeInTheDocument();
    expect(screen.getByText(/mixed_citation_outcomes/)).toBeInTheDocument();
    expect(
      screen.getByText(/config cfg012345678 · input inp987654321 · kb kb1122334455/),
    ).toBeInTheDocument();

    const table = screen.getByRole("table", { name: "Citation decisions" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(within(rows[1]!).getByText("KB-RET-01")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("correct")).toBeInTheDocument();
    expect(within(rows[1]!).getByText(/component coverage: 1, 0\.85/)).toBeInTheDocument();
    expect(within(rows[2]!).getByText("KB-RET-99")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("not_retrieved")).toBeInTheDocument();
  });

  it("renders claim assessment verdicts, abstentions, deciding evidence, and model provenance", () => {
    render(<AssessmentResultCard result={claimFixture} />);

    expect(screen.getByTestId("claim-assessment-disclaimer")).toHaveTextContent(
      /Frozen semantic claim assessment.*semantic-aggregate-v12.*separate from deterministic lexical run metrics/,
    );
    expect(
      screen.getByText(/cross-encoder\/nli-deberta-v3-small \(fa2804872c3b\)/),
    ).toBeInTheDocument();
    expect(screen.getByText("assessed")).toBeInTheDocument();

    const table = screen.getByRole("table", { name: "Claim verdicts" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);

    expect(
      within(rows[1]!).getByText("Standard items can be returned within 30 days."),
    ).toBeInTheDocument();
    expect(within(rows[1]!).getByText("supported")).toBeInTheDocument();
    expect(within(rows[1]!).queryByText("abstained")).not.toBeInTheDocument();
    expect(within(rows[1]!).getByText("KB-RET-01")).toBeInTheDocument();
    expect(within(rows[1]!).getByText(/entails · sentence · s1/)).toBeInTheDocument();

    expect(within(rows[2]!).getByText("needs_human_review")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("abstained")).toBeInTheDocument();
    expect(
      within(rows[2]!).getByText(/guards: exception_guard, numeric_guard/),
    ).toBeInTheDocument();

    // Configured model alone never proves that inference was executed.
    expect(screen.getByText("Configured model")).toBeInTheDocument();
    expect(screen.getByTestId("executed-model")).toHaveTextContent("Not recorded");
    expect(screen.queryByText("Real NLI inference")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Simulated verifier — no NLI model loaded"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("executed-verifier")).not.toBeInTheDocument();
  });

  it("displays 'Simulated verifier — no NLI model loaded' and distinguishes configured model from executed verifier when simulated metadata is present", () => {
    const simulatedClaimResult: ClaimAssessmentResult = {
      ...claimFixture,
      claims: [
        {
          claim_text: "Refunds are issued to the original payment method.",
          label: "supported",
          abstained: false,
          reason:
            "[SIMULATED DEMO VERIFIER — deterministic lexical stand-in; no DeBERTa NLI model loaded] Matched retrieved evidence in KB-RET-01.",
          diagnostic_branch: "simulated_demo_verifier",
          deciding_evidence: [
            {
              passage_id: "KB-RET-01",
              relation: "support",
              scope: "sentence",
              unit: "Refunds are issued to the original payment method within 5 business days.",
            },
          ],
          guards: ["simulated_demo_verifier"],
          config: { question_pass: "not needed", simulated_demo: true },
          model: {
            backend: "simulated_demo_stub",
            model_id: "demo-stub/simulated-lexical-verifier",
            revision: null,
          },
          uncalibrated_scores: [],
          ignored_contradictions: [],
        },
      ],
    };

    const realTransformersResult: ClaimAssessmentResult = {
      ...claimFixture,
      claims: [
        {
          ...claimFixture.claims[0]!,
          model: {
            backend: "transformers",
            model_id: "cross-encoder/nli-deberta-v3-small",
            revision: "fa2804872c3b4bd748f38c0185cc85775361e735",
          },
        },
      ],
    };

    const { rerender } = render(<ClaimAssessmentResultCard result={simulatedClaimResult} />);

    const badge = screen.getByText("Simulated verifier — no NLI model loaded");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveClass("badge-neutral");

    expect(screen.getByText("Configured model")).toBeInTheDocument();
    expect(
      screen.getByText("cross-encoder/nli-deberta-v3-small (fa2804872c3b)"),
    ).toBeInTheDocument();
    expect(screen.getByText("Executed verifier")).toBeInTheDocument();
    expect(screen.getByTestId("executed-verifier")).toHaveTextContent(
      "demo-stub/simulated-lexical-verifier · simulated_demo_stub (simulated — no NLI weights loaded)",
    );

    // Rerendering with a real transformers NLI result restores the standard UI
    rerender(<ClaimAssessmentResultCard result={realTransformersResult} />);
    expect(
      screen.queryByText("Simulated verifier — no NLI model loaded"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Configured model")).toBeInTheDocument();
    expect(screen.getByTestId("inference-backend")).toHaveTextContent("transformers");
    expect(screen.queryByText("Real NLI inference")).not.toBeInTheDocument();
    expect(screen.queryByTestId("executed-verifier")).not.toBeInTheDocument();
  });

  it("identifies real inference only with matching executed model, verified snapshot, and scored pairs", () => {
    const real: ClaimAssessmentResult = {
      ...claimFixture,
      claims: [{
        ...claimFixture.claims[0]!,
        model: {
          backend: "transformers", model_id: claimFixture.model_id,
          revision: claimFixture.model_revision, resolved_snapshot_commit: claimFixture.model_revision,
          snapshot_files: { "model.safetensors": { bytes: 567605820 } },
        },
        uncalibrated_scores: [{ entailment: 0.994845, contradiction: 0.000091, neutral: 0.005064 }],
      }],
    };
    const { rerender } = render(<ClaimAssessmentResultCard result={real} />);
    expect(screen.getByText("Real NLI inference")).toBeInTheDocument();
    expect(screen.getByTestId("executed-model")).toHaveTextContent(real.model_id);
    expect(screen.getByTestId("model-revision")).toHaveTextContent(real.model_revision);
    expect(screen.getByTestId("inference-backend")).toHaveTextContent("transformers");
    expect(screen.queryByTestId("executed-verifier")).not.toBeInTheDocument();
    for (const changes of [
      { uncalibrated_scores: [] },
      { model: { ...real.claims[0]!.model, resolved_snapshot_commit: "0".repeat(40) } },
      { model: { ...real.claims[0]!.model, model_id: "different-model" } },
      { uncalibrated_scores: [{ entailment: 9, contradiction: -8, neutral: 0 }] },
    ]) {
      rerender(<ClaimAssessmentResultCard result={{ ...real, claims: [{ ...real.claims[0]!, ...changes }] }} />);
      expect(screen.queryByText("Real NLI inference")).not.toBeInTheDocument();
    }
  });

  it("handles empty citation and claim results and null knowledge_base_hash gracefully", () => {
    const emptyCitation: CitationAssessmentResult = {
      ...citationFixture,
      knowledge_base_hash: null,
      status: "not_applicable",
      reason_code: "no_citation_expected",
      reason: "No citations were expected or present.",
      per_citation: [],
    };
    const emptyClaim: ClaimAssessmentResult = {
      ...claimFixture,
      knowledge_base_hash: null,
      status: "no_checkable_claims",
      claims: [],
    };

    render(
      <>
        <CitationAssessmentResultCard result={emptyCitation} />
        <ClaimAssessmentResultCard result={emptyClaim} />
      </>,
    );

    expect(
      screen.getByText("No individual citations recorded for this response."),
    ).toBeInTheDocument();
    expect(screen.getByText("No checkable claims extracted from this response.")).toBeInTheDocument();
    expect(screen.getByText("no_checkable_claims")).toBeInTheDocument();
    expect(screen.getAllByText(/kb n\/a/)).toHaveLength(2);
  });

  it("safely renders unknown statuses, null citation IDs, sparse evidence, and long text", () => {
    const longClaim = "A".repeat(600);
    const unusualCitation = {
      ...citationFixture,
      status: "future_citation_status" as CitationAssessmentResult["status"],
      per_citation: [
        {
          citation_id: null,
          status: "future_decision_status" as CitationAssessmentResult["status"],
          reason_code: "missing_marker",
          reason: "Response omitted a citation marker.",
          component_coverage: [],
        },
      ],
    };
    const unusualClaim: ClaimAssessmentResult = {
      ...claimFixture,
      status: "custom_status",
      claims: [
        {
          claim_text: longClaim,
          label: "future_verdict" as ClaimAssessmentResult["claims"][number]["label"],
          abstained: false,
          reason: "Unrecognized verdict label handled without throwing.",
          diagnostic_branch: "fallback_branch",
          deciding_evidence: [{}, { unexpected_field: 123 }],
          guards: [],
          config: {},
          model: {},
          uncalibrated_scores: [],
          ignored_contradictions: [],
        },
      ],
    };

    render(
      <>
        <CitationAssessmentResultCard result={unusualCitation} />
        <ClaimAssessmentResultCard result={unusualClaim} />
      </>,
    );

    expect(screen.getByText("future_citation_status")).toBeInTheDocument();
    expect(screen.getByText("future_decision_status")).toBeInTheDocument();
    expect(screen.getByText("none")).toBeInTheDocument();
    expect(screen.getByText("custom_status")).toBeInTheDocument();
    expect(screen.getByText("future_verdict")).toBeInTheDocument();
    expect(screen.getByText(longClaim)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Deciding evidence" })).not.toBeInTheDocument();
  });
});
