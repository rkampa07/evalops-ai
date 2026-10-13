"""Score individual test cases and aggregate them into run-level summaries."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import fmean
from typing import Any

from . import metrics as m


@dataclass(frozen=True)
class EvalConfig:
    """Per-run scoring policy. Thresholds apply per test case."""

    min_token_f1: float = 0.5
    require_exact_match: bool = False
    case_sensitive: bool = False
    default_max_latency_ms: float | None = None


@dataclass(frozen=True)
class CaseSpec:
    key: str
    expected_output: str | None = None
    required_terms: list[str] = field(default_factory=list)
    forbidden_terms: list[str] = field(default_factory=list)
    json_schema: dict[str, Any] | None = None
    max_latency_ms: float | None = None
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResponseSpec:
    output: str
    latency_ms: float | None = None


@dataclass
class CaseResult:
    key: str
    passed: bool
    metrics: dict[str, Any]
    failures: list[dict[str, str]]
    tags: list[str]


def _failure(check: str, message: str) -> dict[str, str]:
    return {"check": check, "message": message}


def score_case(case: CaseSpec, response: ResponseSpec | None, config: EvalConfig) -> CaseResult:
    """Run every check that applies to the case. The case passes only if every check passes."""
    if response is None:
        return CaseResult(
            key=case.key,
            passed=False,
            metrics={"responded": False},
            failures=[_failure("missing_response", "no response was submitted for this case")],
            tags=list(case.tags),
        )

    cs = config.case_sensitive
    out = response.output
    metrics: dict[str, Any] = {"responded": True}
    failures: list[dict[str, str]] = []

    # Reference-based lexical metrics
    if case.expected_output is not None:
        em = m.exact_match(out, case.expected_output, case_sensitive=cs)
        ts = m.token_scores(out, case.expected_output, case_sensitive=cs)
        metrics |= {
            "exact_match": em,
            "token_precision": round(ts.precision, 6),
            "token_recall": round(ts.recall, 6),
            "token_f1": round(ts.f1, 6),
        }
        if config.require_exact_match and not em:
            failures.append(_failure("exact_match", "normalized output differs from expected"))
        if ts.f1 < config.min_token_f1:
            failures.append(
                _failure("token_f1", f"token F1 {ts.f1:.3f} < threshold {config.min_token_f1:.3f}")
            )

    # Required terms
    if case.required_terms:
        missing = m.missing_terms(out, case.required_terms, case_sensitive=cs)
        metrics["required_terms_missing"] = missing
        metrics["required_terms_passed"] = not missing
        if missing:
            failures.append(_failure("required_terms", f"missing required terms: {missing}"))

    # Forbidden terms
    if case.forbidden_terms:
        found = m.present_terms(out, case.forbidden_terms, case_sensitive=cs)
        metrics["forbidden_terms_found"] = found
        metrics["forbidden_terms_passed"] = not found
        if found:
            failures.append(_failure("forbidden_terms", f"contains forbidden terms: {found}"))

    # JSON schema
    if case.json_schema is not None:
        jc = m.validate_json_output(out, case.json_schema)
        metrics["json_parsed"] = jc.parsed
        metrics["json_valid"] = jc.valid
        metrics["schema_errors"] = jc.errors
        if not jc.valid:
            failures.append(_failure("json_schema", "; ".join(jc.errors)))

    # Latency
    limit = (
        case.max_latency_ms if case.max_latency_ms is not None else config.default_max_latency_ms
    )
    metrics["latency_ms"] = response.latency_ms
    if limit is not None:
        metrics["latency_limit_ms"] = limit
        if response.latency_ms is None:
            metrics["latency_passed"] = False
            failures.append(_failure("latency", "latency limit set but no latency was reported"))
        else:
            ok = response.latency_ms <= limit
            metrics["latency_passed"] = ok
            if not ok:
                failures.append(
                    _failure(
                        "latency", f"latency {response.latency_ms:.0f}ms > limit {limit:.0f}ms"
                    )
                )

    return CaseResult(
        key=case.key, passed=not failures, metrics=metrics, failures=failures, tags=list(case.tags)
    )


def _rate(flags: list[bool]) -> float | None:
    return round(sum(flags) / len(flags), 6) if flags else None


def _mean(values: list[float]) -> float | None:
    return round(fmean(values), 6) if values else None


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    """Aggregate case results. Rates are None when no case applies, never a misleading 0 or 1."""
    responded = [r for r in results if r.metrics.get("responded")]

    def collect(key: str) -> list[Any]:
        return [r.metrics[key] for r in responded if r.metrics.get(key) is not None]

    latencies = [float(v) for v in collect("latency_ms")]
    forbidden_violations = sum(len(r.metrics.get("forbidden_terms_found", [])) for r in responded)

    failure_counts: dict[str, int] = defaultdict(int)
    for r in results:
        for f in r.failures:
            failure_counts[f["check"]] += 1

    tag_flags: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        for tag in r.tags:
            tag_flags[tag].append(r.passed)

    passed = sum(r.passed for r in results)
    return {
        "total_cases": len(results),
        "responded_cases": len(responded),
        "passed_cases": passed,
        "failed_cases": len(results) - passed,
        "pass_rate": _rate([r.passed for r in results]),
        "exact_match_rate": _rate(collect("exact_match")),
        "mean_token_precision": _mean(collect("token_precision")),
        "mean_token_recall": _mean(collect("token_recall")),
        "mean_token_f1": _mean(collect("token_f1")),
        "required_terms_pass_rate": _rate(collect("required_terms_passed")),
        "forbidden_terms_pass_rate": _rate(collect("forbidden_terms_passed")),
        "forbidden_violation_count": forbidden_violations,
        "schema_pass_rate": _rate(collect("json_valid")),
        "latency_pass_rate": _rate(collect("latency_passed")),
        "latency_mean_ms": _mean(latencies),
        "latency_p50_ms": m.percentile(latencies, 50),
        "latency_p95_ms": m.percentile(latencies, 95),
        "latency_max_ms": max(latencies) if latencies else None,
        "failure_counts": dict(sorted(failure_counts.items())),
        "tag_pass_rates": {
            t: {"cases": len(f), "pass_rate": _rate(f)} for t, f in sorted(tag_flags.items())
        },
    }
