"""Verdict types. Scores are raw model probabilities: uncalibrated, not confidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

SUPPORTED = "supported"
CONTRADICTED = "contradicted"
INSUFFICIENT = "insufficient_evidence"
NEEDS_REVIEW = "needs_human_review"
LABELS = (SUPPORTED, CONTRADICTED, INSUFFICIENT, NEEDS_REVIEW)

ENTAILMENT, NEUTRAL, CONTRADICTION = "entailment", "neutral", "contradiction"


@dataclass(frozen=True)
class UnitScore:
    """One evidence unit (a passage sentence or a whole passage) scored against the claim."""

    passage_id: str
    unit: str  # the evidence text itself, quoted in explanations
    scope: str  # "sentence" | "passage"
    scores: dict[str, float]  # {entailment, neutral, contradiction}, uncalibrated

    @property
    def relation(self) -> str:
        return max(self.scores, key=self.scores.get)


@dataclass
class SemanticVerdict:
    label: str
    reason: str
    deciding: list[dict] = field(default_factory=list)  # [{passage_id, unit, relation}]
    guards: list[str] = field(default_factory=list)  # guards that changed the model's label
    model: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    uncalibrated_scores: list[dict] = field(default_factory=list)
    # Model contradictions set aside as not about the claim (unrelated or a different condition).
    ignored_contradictions: list[dict] = field(default_factory=list)
    note: str = (
        "Experimental model judgement (heuristic aggregation over an NLI model). Not verified "
        "semantic correctness; not part of methodology 2.0.0 or release gates. The explanation "
        "quotes the evidence; it is not proof."
    )

    def to_dict(self) -> dict:
        return asdict(self)
