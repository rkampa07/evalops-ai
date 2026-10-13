"""Agreement between a verifier's verdicts and reference labels (binary).

Used to measure how well the lexical heuristic and a model judge detect unsupported claims.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """95% Wilson score interval for a proportion (better than normal approx. for small n)."""
    if n == 0:
        return None, None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(centre - half, 4), round(centre + half, 4)


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 6) if b else None


def binary_agreement(
    labels: Sequence[str], predictions: Sequence[str], positive: str
) -> dict[str, Any]:
    if len(labels) != len(predictions):
        raise ValueError("labels and predictions must have the same length")
    pairs = list(zip(labels, predictions, strict=True))
    tp = sum(lab == positive and pred == positive for lab, pred in pairs)
    fp = sum(lab != positive and pred == positive for lab, pred in pairs)
    fn = sum(lab == positive and pred != positive for lab, pred in pairs)
    tn = len(pairs) - tp - fp - fn
    n = len(pairs)
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = (
        round(2 * precision * recall / (precision + recall), 6)
        if precision is not None and recall is not None and precision + recall > 0
        else None
    )
    po = (tp + tn) / n if n else None
    kappa = None
    if n:
        pe = ((tp + fp) / n) * ((tp + fn) / n) + ((fn + tn) / n) * ((fp + tn) / n)
        kappa = round((po - pe) / (1 - pe), 6) if pe < 1 else None
    return {
        "n": n,
        "positive_class": positive,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "precision_ci95": wilson_interval(tp, tp + fp),
        "recall": recall,
        "recall_ci95": wilson_interval(tp, tp + fn),
        "f1": f1,
        "accuracy": round(po, 6) if po is not None else None,
        "accuracy_ci95": wilson_interval(tp + tn, n),
        "cohen_kappa": kappa,
    }
