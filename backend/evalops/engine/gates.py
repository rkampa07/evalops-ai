"""Configurable release gates evaluated against a run summary and optional comparison.

Policy:
- A gate on a metric that does not apply to the dataset (e.g. schema_pass_rate when no case
  has a schema) is "skipped" and does not block the release.
- A comparison gate configured without a baseline fails closed. A release gate must never
  pass silently because the data it needed was missing.
- A comparison gate fails closed when the baseline is not comparable (different scoring
  config, incompatible methodology, or no case with an identical definition). That is reported
  as "not comparable", never as a regression.
- The decision is "pass" only if no gate failed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Comparator = Literal[">=", "<="]


@dataclass(frozen=True)
class GateConfig:
    min_pass_rate: float | None = None
    min_mean_token_f1: float | None = None
    min_exact_match_rate: float | None = None
    min_required_terms_pass_rate: float | None = None
    min_schema_pass_rate: float | None = None
    min_latency_pass_rate: float | None = None
    max_forbidden_violations: int | None = None
    max_p95_latency_ms: float | None = None
    max_failed_cases: int | None = None
    # Comparison gates: need a baseline run
    max_pass_rate_drop: float | None = None
    max_regressed_cases: int | None = None
    # RAG (deterministic)
    min_retrieval_recall: float | None = None
    min_mrr: float | None = None
    min_citation_validity: float | None = None
    min_groundedness: float | None = None
    max_unsupported_claims: int | None = None
    min_abstention_accuracy: float | None = None
    # RAG (estimated by a model judge)
    min_estimated_groundedness: float | None = None

    def configured(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


# gate name -> (summary key or comparison key, comparator, needs_baseline)
_GATE_SPECS: dict[str, tuple[str, Comparator, bool]] = {
    "min_pass_rate": ("pass_rate", ">=", False),
    "min_mean_token_f1": ("mean_token_f1", ">=", False),
    "min_exact_match_rate": ("exact_match_rate", ">=", False),
    "min_required_terms_pass_rate": ("required_terms_pass_rate", ">=", False),
    "min_schema_pass_rate": ("schema_pass_rate", ">=", False),
    "min_latency_pass_rate": ("latency_pass_rate", ">=", False),
    "max_forbidden_violations": ("forbidden_violation_count", "<=", False),
    "max_p95_latency_ms": ("latency_p95_ms", "<=", False),
    "max_failed_cases": ("failed_cases", "<=", False),
    "max_pass_rate_drop": ("pass_rate_drop", "<=", True),
    "max_regressed_cases": ("regressed_cases", "<=", True),
    "min_retrieval_recall": ("retrieval_recall_at_k", ">=", False),
    "min_mrr": ("mrr", ">=", False),
    "min_citation_validity": ("citation_validity_rate", ">=", False),
    "min_groundedness": ("groundedness", ">=", False),
    "max_unsupported_claims": ("unsupported_claim_count", "<=", False),
    "min_abstention_accuracy": ("abstention_accuracy", ">=", False),
    "min_estimated_groundedness": ("estimated_groundedness", ">=", False),
}

# Gates whose data must be produced separately; missing data fails closed instead of skipping.
_FAIL_IF_MISSING = {
    "min_estimated_groundedness": "no model-based grades attached to this run; failing closed",
}

# Applied by the report endpoint when the run contains RAG cases. Chosen before any model
# output was generated (see docs/rag-evaluation.md).
DEFAULT_RAG_POLICY = {
    "min_retrieval_recall": 0.8,
    "min_citation_validity": 1.0,
    "min_groundedness": 0.8,
    "min_abstention_accuracy": 1.0,
}

DEFAULT_RELEASE_POLICY = GateConfig(
    min_pass_rate=0.9,
    max_forbidden_violations=0,
    min_schema_pass_rate=1.0,
    max_pass_rate_drop=0.0,
    max_regressed_cases=0,
)


def evaluate_gates(
    summary: dict[str, Any],
    gates: GateConfig,
    comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    configured = gates.configured()
    if not configured:
        raise ValueError("at least one gate must be configured")

    comparison_values: dict[str, float] = {}
    incomparable_reason = None
    if comparison is not None and not comparison.get("compatible", True):
        errors = [
            i["message"]
            for i in comparison.get("compatibility_issues", [])
            if i["severity"] == "error"
        ]
        incomparable_reason = "baseline not comparable: " + "; ".join(errors)
    elif comparison is not None:
        pr = next(d for d in comparison["metric_deltas"] if d["metric"] == "pass_rate")
        if pr["baseline"] is not None and pr["candidate"] is not None:
            comparison_values["pass_rate_drop"] = round(
                max(0.0, pr["baseline"] - pr["candidate"]), 6
            )
        comparison_values["regressed_cases"] = comparison["case_counts"]["regressed"]

    results = []
    for name, threshold in configured.items():
        key, comparator, needs_baseline = _GATE_SPECS[name]
        if needs_baseline:
            if comparison is None:
                results.append(
                    _result(
                        name,
                        key,
                        comparator,
                        threshold,
                        None,
                        "fail",
                        "requires a baseline run; failing closed",
                    )
                )
                continue
            if incomparable_reason is not None:
                results.append(
                    _result(name, key, comparator, threshold, None, "fail", incomparable_reason)
                )
                continue
            actual = comparison_values.get(key)
        else:
            actual = summary.get(key)

        if actual is None and name in _FAIL_IF_MISSING:
            results.append(
                _result(name, key, comparator, threshold, None, "fail", _FAIL_IF_MISSING[name])
            )
            continue
        if actual is None:
            results.append(
                _result(
                    name,
                    key,
                    comparator,
                    threshold,
                    None,
                    "skipped",
                    "metric not applicable to this dataset",
                )
            )
            continue
        ok = actual >= threshold if comparator == ">=" else actual <= threshold
        msg = f"{key} = {actual} {'meets' if ok else 'violates'} {comparator} {threshold}"
        results.append(
            _result(name, key, comparator, threshold, actual, "pass" if ok else "fail", msg)
        )

    failed = [r for r in results if r["status"] == "fail"]
    return {
        "decision": "fail" if failed else "pass",
        "passed_gates": sum(r["status"] == "pass" for r in results),
        "failed_gates": len(failed),
        "skipped_gates": sum(r["status"] == "skipped" for r in results),
        "gates": results,
    }


def _result(name, metric, comparator, threshold, actual, status, message) -> dict[str, Any]:
    return {
        "gate": name,
        "metric": metric,
        "comparator": comparator,
        "threshold": threshold,
        "actual": actual,
        "status": status,
        "message": message,
    }
