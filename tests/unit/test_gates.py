import pytest

from evalops.engine.gates import DEFAULT_RELEASE_POLICY, GateConfig, evaluate_gates

SUMMARY = {
    "pass_rate": 0.92,
    "mean_token_f1": 0.81,
    "exact_match_rate": None,
    "required_terms_pass_rate": 1.0,
    "schema_pass_rate": None,
    "latency_pass_rate": 1.0,
    "forbidden_violation_count": 0,
    "latency_p95_ms": 900.0,
    "failed_cases": 1,
}


def comparison(baseline_pass=0.95, candidate_pass=0.92, regressed=1):
    return {
        "metric_deltas": [
            {"metric": "pass_rate", "baseline": baseline_pass, "candidate": candidate_pass}
        ],
        "case_counts": {"regressed": regressed},
    }


def by_gate(result):
    return {g["gate"]: g for g in result["gates"]}


def test_all_pass():
    r = evaluate_gates(SUMMARY, GateConfig(min_pass_rate=0.9, max_p95_latency_ms=1000))
    assert r["decision"] == "pass" and r["passed_gates"] == 2


def test_threshold_boundaries_are_inclusive():
    r = evaluate_gates(SUMMARY, GateConfig(min_pass_rate=0.92, max_p95_latency_ms=900))
    assert r["decision"] == "pass"


def test_single_failure_fails_decision():
    r = evaluate_gates(SUMMARY, GateConfig(min_pass_rate=0.95, max_failed_cases=5))
    assert r["decision"] == "fail" and r["failed_gates"] == 1
    assert by_gate(r)["min_pass_rate"]["status"] == "fail"


def test_not_applicable_metric_is_skipped_not_failed():
    r = evaluate_gates(SUMMARY, GateConfig(min_schema_pass_rate=1.0, min_pass_rate=0.5))
    assert by_gate(r)["min_schema_pass_rate"]["status"] == "skipped"
    assert r["decision"] == "pass"


def test_comparison_gate_without_baseline_fails_closed():
    r = evaluate_gates(SUMMARY, GateConfig(max_regressed_cases=0))
    g = by_gate(r)["max_regressed_cases"]
    assert g["status"] == "fail" and "baseline" in g["message"]
    assert r["decision"] == "fail"


def test_comparison_gates_with_baseline():
    r = evaluate_gates(
        SUMMARY, GateConfig(max_pass_rate_drop=0.05, max_regressed_cases=0), comparison()
    )
    g = by_gate(r)
    assert g["max_pass_rate_drop"]["actual"] == pytest.approx(0.03)
    assert g["max_pass_rate_drop"]["status"] == "pass"
    assert g["max_regressed_cases"]["status"] == "fail"


def test_pass_rate_improvement_is_zero_drop():
    r = evaluate_gates(SUMMARY, GateConfig(max_pass_rate_drop=0.0), comparison(0.8, 0.92, 0))
    assert by_gate(r)["max_pass_rate_drop"]["actual"] == 0.0


def test_no_gates_rejected():
    with pytest.raises(ValueError):
        evaluate_gates(SUMMARY, GateConfig())


def test_default_policy_is_strict_on_safety():
    assert DEFAULT_RELEASE_POLICY.max_forbidden_violations == 0
    assert DEFAULT_RELEASE_POLICY.max_regressed_cases == 0


def test_incomparable_baseline_fails_comparison_gates_closed():
    incomparable = {
        "compatible": False,
        "compatibility_issues": [
            {"severity": "error", "kind": "scoring_config", "message": "configs differ"}
        ],
        "metric_deltas": [],
        "case_counts": {"regressed": 0},
    }
    r = evaluate_gates(SUMMARY, GateConfig(min_pass_rate=0.5, max_regressed_cases=0), incomparable)
    g = by_gate(r)
    assert g["min_pass_rate"]["status"] == "pass"
    assert g["max_regressed_cases"]["status"] == "fail"
    assert "not comparable" in g["max_regressed_cases"]["message"]
    assert r["decision"] == "fail"
