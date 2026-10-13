"""Golden-score regression tests.

These lock the engine's output on the committed example fixtures. If a change to the
scoring code shifts any of these numbers, the test fails. Update the expected values
only after deliberately reviewing the methodology change.
"""

import pytest

from conftest import load_example
from evalops.engine.regression import CaseSnapshot, compare_runs
from evalops.engine.scoring import CaseSpec, EvalConfig, ResponseSpec, score_case, summarize

DATASET = load_example("datasets/kestrel_support_v1.json")


def score(response_file):
    data = load_example(f"responses/{response_file}.json")
    cfg = EvalConfig(**data["config"])
    responses = {
        r["case_key"]: ResponseSpec(r["output"], r["latency_ms"]) for r in data["responses"]
    }
    results = []
    for c in DATASET["test_cases"]:
        spec = CaseSpec(
            key=c["key"],
            expected_output=c.get("expected_output"),
            required_terms=c.get("required_terms", []),
            forbidden_terms=c.get("forbidden_terms", []),
            json_schema=c.get("json_schema"),
            max_latency_ms=c.get("max_latency_ms"),
            tags=c.get("tags", []),
        )
        results.append(score_case(spec, responses.get(c["key"]), cfg))
    return results, summarize(results)


GOLDEN = {
    "baseline_prod_v1.3": {
        "passed_cases": 11,
        "pass_rate": 0.916667,
        "mean_token_f1": 0.919433,
        "schema_pass_rate": 1.0,
        "forbidden_violation_count": 0,
        "latency_p95_ms": 1659.5,
        "failed": {"policy-phone-hours"},
    },
    "candidate_v1.4_regressed": {
        "passed_cases": 8,
        "pass_rate": 0.666667,
        "mean_token_f1": 0.792578,
        "schema_pass_rate": 0.333333,
        "forbidden_violation_count": 2,
        "latency_p95_ms": 694.5,
        "failed": {
            "policy-damaged-item",
            "extract-order-not-delivered",
            "extract-order-wrong-item",
            "safety-unauthorized-compensation",
        },
    },
    "candidate_v1.5_fixed": {
        "passed_cases": 12,
        "pass_rate": 1.0,
        "mean_token_f1": 0.99359,
        "schema_pass_rate": 1.0,
        "forbidden_violation_count": 0,
        "latency_p95_ms": 714.5,
        "failed": set(),
    },
}


@pytest.mark.parametrize("name", list(GOLDEN))
def test_golden_summary(name):
    results, s = score(name)
    expected = GOLDEN[name]
    for key in (
        "passed_cases",
        "pass_rate",
        "mean_token_f1",
        "schema_pass_rate",
        "forbidden_violation_count",
        "latency_p95_ms",
    ):
        assert s[key] == pytest.approx(expected[key], abs=1e-6), key
    assert {r.key for r in results if not r.passed} == expected["failed"]


def test_scoring_is_deterministic():
    assert score("candidate_v1.4_regressed")[1] == score("candidate_v1.4_regressed")[1]


def test_golden_regression_verdicts():
    def snaps(results):
        return {
            r.key: CaseSnapshot(r.passed, r.metrics.get("token_f1"), r.failures) for r in results
        }

    base_r, base_s = score("baseline_prod_v1.3")
    bad_r, bad_s = score("candidate_v1.4_regressed")
    good_r, good_s = score("candidate_v1.5_fixed")
    assert compare_runs(base_s, bad_s, snaps(base_r), snaps(bad_r))["has_regression"] is True
    assert compare_runs(base_s, good_s, snaps(base_r), snaps(good_r))["has_regression"] is False
