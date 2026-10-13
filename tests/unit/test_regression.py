from evalops.engine.regression import CaseSnapshot, CompareConfig, compare_runs


def summary(**overrides):
    base = {
        "pass_rate": 0.9,
        "exact_match_rate": 0.5,
        "mean_token_f1": 0.8,
        "required_terms_pass_rate": 1.0,
        "forbidden_terms_pass_rate": 1.0,
        "schema_pass_rate": None,
        "latency_pass_rate": 1.0,
        "latency_p95_ms": 1000.0,
    }
    return base | overrides


def snap(passed, f1=None):
    return CaseSnapshot(passed=passed, token_f1=f1, failures=[])


def deltas(result):
    return {d["metric"]: d for d in result["metric_deltas"]}


def test_identical_runs_have_no_regression():
    cases = {"a": snap(True, 0.9)}
    r = compare_runs(summary(), summary(), cases, cases)
    assert not r["has_regression"] and r["regression_reasons"] == []
    assert r["case_counts"]["unchanged_pass"] == 1


def test_case_flip_pass_to_fail_is_regression():
    r = compare_runs(
        summary(),
        summary(),
        {"a": snap(True), "b": snap(False)},
        {"a": snap(False), "b": snap(True)},
    )
    assert r["has_regression"]
    changes = {c["case_key"]: c["change"] for c in r["case_changes"]}
    assert changes == {"a": "regressed", "b": "fixed"}
    assert "1 case(s) went from pass to fail" in r["regression_reasons"]


def test_metric_drop_within_tolerance_is_unchanged():
    r = compare_runs(summary(mean_token_f1=0.80), summary(mean_token_f1=0.79), {}, {})
    assert deltas(r)["mean_token_f1"]["status"] == "unchanged"
    assert not r["has_regression"]


def test_metric_drop_beyond_tolerance_is_regression():
    r = compare_runs(summary(mean_token_f1=0.80), summary(mean_token_f1=0.70), {}, {})
    d = deltas(r)["mean_token_f1"]
    assert d["status"] == "regressed" and d["delta"] == -0.1
    assert r["has_regression"]


def test_improvement_detected():
    r = compare_runs(summary(pass_rate=0.8), summary(pass_rate=0.95), {}, {})
    assert deltas(r)["pass_rate"]["status"] == "improved"


def test_latency_uses_relative_tolerance_and_lower_is_better():
    cfg = CompareConfig(latency_tolerance_pct=0.2)
    ok = compare_runs(summary(latency_p95_ms=1000.0), summary(latency_p95_ms=1150.0), {}, {}, cfg)
    assert deltas(ok)["latency_p95_ms"]["status"] == "unchanged"
    bad = compare_runs(summary(latency_p95_ms=1000.0), summary(latency_p95_ms=1300.0), {}, {}, cfg)
    assert deltas(bad)["latency_p95_ms"]["status"] == "regressed"
    faster = compare_runs(
        summary(latency_p95_ms=1000.0), summary(latency_p95_ms=500.0), {}, {}, cfg
    )
    assert deltas(faster)["latency_p95_ms"]["status"] == "improved"


def test_not_applicable_metrics():
    r = compare_runs(summary(), summary(), {}, {})
    assert deltas(r)["schema_pass_rate"]["status"] == "not_applicable"


def test_f1_drop_flagged_but_not_counted_as_regression_reason():
    r = compare_runs(
        summary(),
        summary(),
        {"a": snap(True, 0.95)},
        {"a": snap(True, 0.6)},
        CompareConfig(case_f1_drop_threshold=0.15),
    )
    assert r["case_changes"][0]["change"] == "f1_drop"
    assert r["case_changes"][0]["token_f1_delta"] == -0.35
    assert not r["has_regression"]


def test_cases_only_in_one_run():
    r = compare_runs(summary(), summary(), {"a": snap(True)}, {"b": snap(True)})
    assert r["case_counts"]["only_in_baseline"] == 1
    assert r["case_counts"]["only_in_candidate"] == 1


def test_excluded_cases_are_listed_but_never_regressions():
    r = compare_runs(
        summary(),
        summary(),
        {"a": snap(True), "b": snap(True)},
        {"a": snap(False), "b": snap(True)},
        excluded={"a": "definition_changed"},
    )
    assert r["compatible"] is True and r["comparable_cases"] == 1
    assert not r["has_regression"]
    assert {c["case_key"]: c["change"] for c in r["case_changes"]} == {
        "a": "definition_changed",
        "b": "unchanged_pass",
    }
    assert r["case_counts"]["definition_changed"] == 1


def test_error_issue_makes_runs_incomparable():
    issue = {"severity": "error", "kind": "scoring_config", "message": "configs differ"}
    r = compare_runs(
        summary(pass_rate=1.0),
        summary(pass_rate=0.0),
        {"a": snap(True)},
        {"a": snap(False)},
        issues=[issue],
    )
    assert r["compatible"] is False and r["has_regression"] is False
    assert r["metric_deltas"] == [] and r["case_changes"] == []
    assert r["compatibility_issues"] == [issue]


def test_warning_issue_keeps_runs_comparable():
    issue = {"severity": "warning", "kind": "methodology_version", "message": "minor"}
    r = compare_runs(summary(), summary(), {"a": snap(True)}, {"a": snap(False)}, issues=[issue])
    assert r["compatible"] is True and r["has_regression"] is True
