"""verify(question, claim, evidence): claim-level semantic verdict over the retrieved evidence.

The question is context, never evidence. Pass 1 judges the claim against the evidence alone. Only
if that is not decisive, and include_question is set, pass 2 adds the question as context (R1:
"I bought it 5 days ago"). A one-sided question-only signal cannot establish the claim and
remains insufficient; conflicting question-context signals go to human review.
"""

from __future__ import annotations

from collections.abc import Mapping

from .aggregate import AggregationConfig, aggregate, decisive, screen_contradictions
from .segments import evidence_units
from .verdict import INSUFFICIENT, NEEDS_REVIEW, SemanticVerdict, UnitScore

__all__ = ["SemanticVerifier", "evidence_units"]


class SemanticVerifier:
    def __init__(self, backend, config: AggregationConfig | None = None):
        self.backend = backend
        self.config = config or AggregationConfig()

    @staticmethod
    def context_premise(question: str, unit: str) -> str:
        return f"Customer question (unverified context): {question.strip()}\nPolicy: {unit}"

    def _score(self, units, claim: str, question: str | None) -> list[UnitScore]:
        premises = [self.context_premise(question, u) if question else u for _, u, _ in units]
        scores = self.backend.score([(p, claim) for p in premises]) if units else []
        return [
            UnitScore(pid, u, scope, s) for (pid, u, scope), s in zip(units, scores, strict=True)
        ]

    def verify(self, question: str, claim: str, evidence: Mapping[str, str]) -> SemanticVerdict:
        """Citations are deliberately not an input: citation validity is a separate check."""
        units = evidence_units(evidence)
        verdict = aggregate(claim, self._score(units, claim, None), evidence, self.config)
        verdict.config["question_pass"] = "not needed"
        if (
            verdict.label == INSUFFICIENT
            and not verdict.guards
            and self.config.include_question
            and question.strip()
        ):
            with_question = self._score(units, claim, question)
            entailing, contradicting = decisive(with_question, self.config)
            contradicting, ignored = screen_contradictions(claim, contradicting)
            verdict.ignored_contradictions += [i | {"with_question": True} for i in ignored]
            verdict.config["question_pass"] = "run"
            if entailing or contradicting:
                verdict.label = NEEDS_REVIEW if entailing and contradicting else INSUFFICIENT
                verdict.guards.append("question_context")
                if entailing and contradicting:
                    e = max(entailing, key=lambda x: x.scores["entailment"])
                    c = max(contradicting, key=lambda x: x.scores["contradiction"])
                    verdict.reason = (
                        "The evidence alone doesn't settle the claim, and with the customer's own "
                        "(unverified) statements as context the evidence conflicts: "
                        f'{e.passage_id} ("{e.unit}") was judged to support it and '
                        f'{c.passage_id} ("{c.unit}") to contradict it. A person should decide.'
                    )
                    verdict.deciding = [
                        {"passage_id": e.passage_id, "unit": e.unit, "scope": e.scope,
                         "relation": "supports (with question)"},
                        {"passage_id": c.passage_id, "unit": c.unit, "scope": c.scope,
                         "relation": "contradicts (with question)"},
                    ]  # fmt: skip
                else:
                    key = "entailment" if entailing else "contradiction"
                    u = max(entailing or contradicting, key=lambda x: x.scores[key])
                    relation = "support" if entailing else "contradict"
                    verdict.reason = (
                        "The evidence alone doesn't settle the claim. Only when the customer's own "
                        f"statements are assumed true (R1 context, unverified) was {u.passage_id} "
                        f'("{u.unit}") judged to {relation} it. Confirm the customer\'s situation.'
                    )
                    verdict.deciding = [
                        {"passage_id": u.passage_id, "unit": u.unit, "scope": u.scope,
                         "relation": f"{relation}s (with question)"}
                    ]  # fmt: skip
                verdict.uncalibrated_scores += [
                    {"passage_id": x.passage_id, "scope": x.scope, "with_question": True} | x.scores
                    for x in with_question
                ]
        verdict.model = self.backend.describe()
        verdict.config["digest"] = self.config.digest()
        return verdict
