"""Compare a baseline run against a candidate run on the same dataset."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Direction = Literal["higher_is_better", "lower_is_better"]

# Aggregate metrics tracked for regressions: (summary key, direction, tolerance kind)
TRACKED_METRICS: list[tuple[str, Direction, Literal["absolute", "relative"]]] = [
    ("pass_rate", "higher_is_better", "absolute"),
    ("exact_match_rate", "higher_is_better", "absolute"),
    ("mean_token_f1", "higher_is_better", "absolute"),
    ("required_terms_pass_rate", "higher_is_better", "absolute"),
    ("forbidden_terms_pass_rate", "higher_is_better", "absolute"),
    ("schema_pass_rate", "higher_is_better", "absolute"),
    ("latency_pass_rate", "higher_is_better", "absolute"),
    ("latency_p95_ms", "lower_is_better", "relative"),
]


@dataclass(frozen=True)
class CompareConfig:
    # Absolute drop tolerated for rate/score metrics in [0, 1] before flagging a regression.
    metric_tolerance: float = 0.02
    # Relative increase tolerated for latency metrics (0.2 = +20%).
    latency_tolerance_pct: float = 0.2
    # A case whose token F1 drops by more than this is flagged as "f1_drop" even if still passing.
    case_f1_drop_threshold: float = 0.15


@dataclass(frozen=True)
class CaseSnapshot:
    passed: bool
    token_f1: float | None
    failures: list[dict[str, str]]


def _metric_status(
    baseline: float | None,
    candidate: float | None,
    direction: Direction,
    kind: str,
    cfg: CompareConfig,
) -> tuple[float | None, str]:
    if baseline is None or candidate is None:
        return None, "not_applicable"
    delta = candidate - baseline
    if kind == "relative":
        tolerance = abs(baseline) * cfg.latency_tolerance_pct
    else:
        tolerance = cfg.metric_tolerance
    signed = delta if direction == "higher_is_better" else -delta
    if signed < -tolerance - 1e-12:
        status = "regressed"
    elif signed > tolerance + 1e-12:
        status = "improved"
    else:
        status = "unchanged"
    return round(delta, 6), status


def compare_runs(
    baseline_summary: dict[str, Any],
    candidate_summary: dict[str, Any],
    baseline_cases: dict[str, CaseSnapshot],
    candidate_cases: dict[str, CaseSnapshot],
    cfg: CompareConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or CompareConfig()

    metric_deltas = []
    for key, direction, kind in TRACKED_METRICS:
        b, c = baseline_summary.get(key), candidate_summary.get(key)
        delta, status = _metric_status(b, c, direction, kind, cfg)
        metric_deltas.append(
            {
                "metric": key,
                "baseline": b,
                "candidate": c,
                "delta": delta,
                "direction": direction,
                "status": status,
            }
        )

    case_changes = []
    counts: dict[str, int] = {
        "regressed": 0,
        "fixed": 0,
        "f1_drop": 0,
        "unchanged_pass": 0,
        "unchanged_fail": 0,
        "only_in_baseline": 0,
        "only_in_candidate": 0,
    }
    for key in sorted(set(baseline_cases) | set(candidate_cases)):
        b, c = baseline_cases.get(key), candidate_cases.get(key)
        f1_delta = None
        if b is None:
            change = "only_in_candidate"
        elif c is None:
            change = "only_in_baseline"
        else:
            if b.token_f1 is not None and c.token_f1 is not None:
                f1_delta = round(c.token_f1 - b.token_f1, 6)
            if b.passed and not c.passed:
                change = "regressed"
            elif not b.passed and c.passed:
                change = "fixed"
            elif f1_delta is not None and f1_delta < -cfg.case_f1_drop_threshold:
                change = "f1_drop"
            else:
                change = "unchanged_pass" if c.passed else "unchanged_fail"
        counts[change] += 1
        case_changes.append(
            {
                "case_key": key,
                "change": change,
                "baseline_passed": b.passed if b else None,
                "candidate_passed": c.passed if c else None,
                "baseline_token_f1": b.token_f1 if b else None,
                "candidate_token_f1": c.token_f1 if c else None,
                "token_f1_delta": f1_delta,
                "candidate_failures": c.failures if c else [],
            }
        )

    reasons = []
    if counts["regressed"]:
        reasons.append(f"{counts['regressed']} case(s) went from pass to fail")
    for d in metric_deltas:
        if d["status"] == "regressed":
            reasons.append(f"{d['metric']} regressed: {d['baseline']} -> {d['candidate']}")

    return {
        "has_regression": bool(reasons),
        "regression_reasons": reasons,
        "metric_deltas": metric_deltas,
        "case_counts": counts,
        "case_changes": case_changes,
        "config": {
            "metric_tolerance": cfg.metric_tolerance,
            "latency_tolerance_pct": cfg.latency_tolerance_pct,
            "case_f1_drop_threshold": cfg.case_f1_drop_threshold,
        },
    }
