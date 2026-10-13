from evalops.engine.scoring import CaseSpec, EvalConfig, ResponseSpec, score_case, summarize

CFG = EvalConfig()


def checks(result):
    return [f["check"] for f in result.failures]


def test_missing_response_fails():
    r = score_case(CaseSpec(key="a", expected_output="x"), None, CFG)
    assert not r.passed and checks(r) == ["missing_response"]
    assert r.metrics == {"responded": False}


def test_reference_case_passes_above_f1_threshold():
    r = score_case(
        CaseSpec(key="a", expected_output="the cat sat down"), ResponseSpec("the cat sat"), CFG
    )
    assert r.passed
    assert r.metrics["exact_match"] is False
    assert 0.85 < r.metrics["token_f1"] < 0.86


def test_low_f1_fails():
    r = score_case(
        CaseSpec(key="a", expected_output="refund in 30 days"), ResponseSpec("hello"), CFG
    )
    assert not r.passed and checks(r) == ["token_f1"]


def test_require_exact_match():
    cfg = EvalConfig(require_exact_match=True)
    r = score_case(CaseSpec(key="a", expected_output="Yes."), ResponseSpec("Yes indeed"), cfg)
    assert "exact_match" in checks(r)
    assert score_case(CaseSpec(key="a", expected_output="Yes."), ResponseSpec("yes"), cfg).passed


def test_required_and_forbidden_terms():
    case = CaseSpec(key="a", required_terms=["specialist"], forbidden_terms=["guarantee", "500"])
    bad = score_case(case, ResponseSpec("We guarantee $500"), CFG)
    assert checks(bad) == ["required_terms", "forbidden_terms"]
    assert bad.metrics["required_terms_missing"] == ["specialist"]
    assert bad.metrics["forbidden_terms_found"] == ["guarantee", "500"]
    good = score_case(case, ResponseSpec("A specialist will help."), CFG)
    assert good.passed and good.metrics["forbidden_terms_passed"] is True


def test_json_schema_check():
    case = CaseSpec(key="a", json_schema={"type": "object", "required": ["x"]})
    assert score_case(case, ResponseSpec('{"x": 1}'), CFG).passed
    bad = score_case(case, ResponseSpec('sure! {"x": 1}'), CFG)
    assert checks(bad) == ["json_schema"] and bad.metrics["json_parsed"] is False


def test_latency_case_limit_overrides_default():
    cfg = EvalConfig(default_max_latency_ms=5000)
    case = CaseSpec(key="a", required_terms=["hi"], max_latency_ms=100)
    r = score_case(case, ResponseSpec("hi", latency_ms=150), cfg)
    assert checks(r) == ["latency"] and r.metrics["latency_limit_ms"] == 100


def test_latency_default_applies_and_missing_latency_fails_closed():
    cfg = EvalConfig(default_max_latency_ms=1000)
    case = CaseSpec(key="a", required_terms=["hi"])
    assert score_case(case, ResponseSpec("hi", latency_ms=999), cfg).passed
    r = score_case(case, ResponseSpec("hi", latency_ms=None), cfg)
    assert checks(r) == ["latency"]


def test_no_latency_limit_means_no_latency_check():
    r = score_case(CaseSpec(key="a", required_terms=["hi"]), ResponseSpec("hi", 99999), CFG)
    assert r.passed and "latency_passed" not in r.metrics


def test_summary_rates_are_none_when_not_applicable():
    results = [score_case(CaseSpec(key="a", required_terms=["x"]), ResponseSpec("x"), CFG)]
    s = summarize(results)
    assert s["pass_rate"] == 1.0
    assert s["schema_pass_rate"] is None
    assert s["mean_token_f1"] is None
    assert s["latency_p95_ms"] is None


def test_summary_aggregates_and_tags():
    cases = [
        (CaseSpec(key="a", expected_output="a b", tags=["t1"]), ResponseSpec("a b", 100)),
        (CaseSpec(key="b", expected_output="a b", tags=["t1", "t2"]), ResponseSpec("zzz", 300)),
        (CaseSpec(key="c", forbidden_terms=["bad"], tags=["t2"]), ResponseSpec("bad bad", 200)),
        (CaseSpec(key="d", json_schema={"type": "object"}), None),
    ]
    s = summarize([score_case(c, r, CFG) for c, r in cases])
    assert s["total_cases"] == 4 and s["responded_cases"] == 3
    assert s["passed_cases"] == 1 and s["pass_rate"] == 0.25
    assert s["exact_match_rate"] == 0.5
    assert s["mean_token_f1"] == 0.5
    assert s["forbidden_violation_count"] == 1
    assert s["forbidden_terms_pass_rate"] == 0.0
    assert s["schema_pass_rate"] is None  # the only schema case had no response
    assert s["latency_p50_ms"] == 200.0 and s["latency_max_ms"] == 300.0
    assert s["failure_counts"] == {"forbidden_terms": 1, "missing_response": 1, "token_f1": 1}
    assert s["tag_pass_rates"]["t1"] == {"cases": 2, "pass_rate": 0.5}
    assert s["tag_pass_rates"]["t2"] == {"cases": 2, "pass_rate": 0.0}
