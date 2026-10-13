"""Configurable release gates evaluated against a run summary and optional comparison.

Policy:
- A gate on a metric that does not apply to the dataset (e.g. schema_pass_rate when no case
  has a schema) is "skipped" and does not block the release.
- A comparison gate configured without a baseline fails closed. A release gate must never
  pass silently because the data it needed was missing.
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
    if comparison is not None:
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
            actual = comparison_values.get(key)
        else:
            actual = summary.get(key)

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
