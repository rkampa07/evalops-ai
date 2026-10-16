"""Metrics for claim-level semantic evaluation with four evaluator outputs.

Gold labels: supported (S), contradicted (C), insufficient_evidence (I).
Evaluator outputs: S, C, I, or needs_review (R) = the evaluator defers to a human.

Every denominator includes deferrals, so an evaluator can't look accurate by deferring:
accuracy is over ALL examples, contradiction recall counts R as a miss, and supported-answer
acceptance counts R as not accepted. Selective accuracy (over decided examples only) is reported
only next to coverage. Proportions come with Wilson 95% intervals.

Baselines that can't express all labels are scored only on what they can say:
- lexical 2.0.0 outputs supported / not_supported -> binary metrics; contradiction "n/a".
- chat diagnostics output warnings -> warning coverage only; "no warning" is never "supported".

Run `python score.py --self-test` to check the definitions on a toy example.
"""

from __future__ import annotations

import sys
from collections import Counter
from math import sqrt

S, C, I, R = "supported", "contradicted", "insufficient_evidence", "needs_review"
GOLD = (S, C, I)
OUTPUTS = (S, C, I, R)


def wilson(k: int, n: int, z: float = 1.96) -> dict:
    if n == 0:
        return {"k": 0, "n": 0, "rate": None, "ci95": None}
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return {"k": k, "n": n, "rate": round(p, 4), "ci95": [round(centre - half, 4), round(centre + half, 4)]}


def score_semantic(examples: list[dict], predictions: dict[str, str]) -> dict:
    """examples: [{id, gold, category}], predictions: {id: one of OUTPUTS}. Missing = error."""
    missing = [e["id"] for e in examples if predictions.get(e["id"]) not in OUTPUTS]
    if missing:
        raise ValueError(f"missing or invalid predictions: {missing}")
    pairs = [(e["gold"], predictions[e["id"]]) for e in examples]
    confusion = {g: {o: 0 for o in OUTPUTS} for g in GOLD}
    for g, o in pairs:
        confusion[g][o] += 1
    n = len(pairs)
    gold_n = Counter(g for g, _ in pairs)
    decided = [(g, o) for g, o in pairs if o != R]
    predicted_c = sum(1 for _, o in pairs if o == C)
    bad = gold_n[C] + gold_n[I]  # claims that must not be approved
    result = {
        "n": n,
        "confusion_gold_x_output": confusion,
        "accuracy_all": wilson(sum(g == o for g, o in pairs), n),
        "coverage_decided": wilson(len(decided), n),
        "review_rate": wilson(n - len(decided), n),
        "selective_accuracy_decided_only": wilson(sum(g == o for g, o in decided), len(decided)),
        "contradiction_recall": wilson(confusion[C][C], gold_n[C]),
        "contradiction_precision": wilson(confusion[C][C], predicted_c),
        "false_approvals": wilson(confusion[C][S] + confusion[I][S], bad),
        "false_approvals_of_contradicted": wilson(confusion[C][S], gold_n[C]),
        "false_rejections_hard": wilson(confusion[S][C], gold_n[S]),
        "false_rejections_soft": wilson(confusion[S][I], gold_n[S]),
        "supported_acceptance": wilson(confusion[S][S], gold_n[S]),
        "review_rate_by_gold": {g: wilson(confusion[g][R], gold_n[g]) for g in GOLD},
        "by_category": {},
    }
    for cat in sorted({e["category"] for e in examples}):
        sub = [(e["gold"], predictions[e["id"]]) for e in examples if e["category"] == cat]
        result["by_category"][cat] = {
            "n": len(sub),
            "correct": sum(g == o for g, o in sub),
            "deferred": sum(o == R for _, o in sub),
            "false_approvals": sum(g != S and o == S for g, o in sub),
            "errors": [f"{g}->{o}" for g, o in sub if g != o],
        }
    return result


def score_binary_lexical(examples: list[dict], supported: dict[str, bool]) -> dict:
    """Lexical 2.0.0: supported or not. It can't distinguish contradicted from insufficient."""
    gold_s = [e for e in examples if e["gold"] == S]
    bad = [e for e in examples if e["gold"] != S]
    return {
        "supported_acceptance": wilson(sum(supported[e["id"]] for e in gold_s), len(gold_s)),
        "false_approvals": wilson(sum(supported[e["id"]] for e in bad), len(bad)),
        "contradiction_recall": "n/a (cannot express contradicted)",
    }


def score_warning_coverage(examples: list[dict], warnings: dict[str, set[str]]) -> dict:
    """chat-diagnostics: coverage of warnings, NOT three-class accuracy."""
    by = {g: [e for e in examples if e["gold"] == g] for g in GOLD}
    any_w = lambda e: bool(warnings.get(e["id"]))  # noqa: E731
    contra = lambda e: "potential_contradiction" in warnings.get(e["id"], set())  # noqa: E731
    return {
        "contradicted_with_contradiction_warning": wilson(sum(map(contra, by[C])), len(by[C])),
        "contradicted_with_any_warning": wilson(sum(map(any_w, by[C])), len(by[C])),
        "insufficient_with_any_warning": wilson(sum(map(any_w, by[I])), len(by[I])),
        "supported_with_any_warning": wilson(sum(map(any_w, by[S])), len(by[S])),
        "supported_with_contradiction_warning": wilson(sum(map(contra, by[S])), len(by[S])),
        "note": "No warning is not a 'supported' verdict; this is not three-class accuracy.",
    }


def _self_test() -> None:
    ex = [{"id": f"e{i}", "gold": g, "category": "x"} for i, g in enumerate([S, S, C, C, I])]
    # An evaluator that defers on everything must not look good.
    all_review = score_semantic(ex, {e["id"]: R for e in ex})
    assert all_review["accuracy_all"]["rate"] == 0 and all_review["contradiction_recall"]["rate"] == 0
    assert all_review["supported_acceptance"]["rate"] == 0 and all_review["review_rate"]["rate"] == 1
    assert all_review["selective_accuracy_decided_only"]["n"] == 0
    # Approving everything: perfect acceptance, all bad claims approved.
    yes = score_semantic(ex, {e["id"]: S for e in ex})
    assert yes["supported_acceptance"]["rate"] == 1 and yes["false_approvals"]["rate"] == 1
    mixed = score_semantic(ex, {"e0": S, "e1": C, "e2": C, "e3": R, "e4": S})
    assert mixed["contradiction_recall"]["k"] == 1 and mixed["contradiction_recall"]["n"] == 2
    assert mixed["contradiction_precision"]["k"] == 1 and mixed["contradiction_precision"]["n"] == 2
    assert mixed["false_rejections_hard"]["k"] == 1 and mixed["false_approvals"]["k"] == 1
    assert mixed["accuracy_all"]["k"] == 2 and mixed["accuracy_all"]["n"] == 5
    assert mixed["selective_accuracy_decided_only"]["n"] == 4
    cov = score_warning_coverage(ex, {"e2": {"potential_contradiction"}, "e0": {"citation_concern"}})
    assert cov["contradicted_with_contradiction_warning"]["k"] == 1
    assert cov["supported_with_any_warning"]["k"] == 1
    print("score.py self-test passed")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
