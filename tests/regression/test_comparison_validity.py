"""Regression tests for two comparison-validity bugs.

Bug 1: two runs with identical outputs but different scoring configs (e.g. min_token_f1)
       were reported as a model regression.
Bug 2: two runs with identical outputs where a test case was edited between them were
       reported as a model regression.

In both situations the model did not change, so the comparison must never claim a regression.
"""

import pytest

REFERENCE = "you can return items within 30 days for a refund"
OUTPUT = "returns accepted within 30 days"  # token F1 vs REFERENCE = 0.4


def make_dataset(client, cases):
    ds = client.post("/datasets", json={"name": "validity"}).json()
    created = client.post(f"/datasets/{ds['id']}/test-cases/bulk", json={"test_cases": cases})
    assert created.status_code == 201, created.text
    return ds, {c["key"]: c for c in created.json()}


def run(client, ds_id, responses, config=None, prompt="same"):
    body = {
        "dataset_id": ds_id,
        "model_name": "m",
        "prompt_version": prompt,
        "config": config or {},
        "responses": [
            {"case_key": k, "output": v, "latency_ms": 100} for k, v in responses.items()
        ],
    }
    r = client.post("/evaluations/runs", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def compare(client, baseline, candidate):
    r = client.get(
        "/evaluations/compare",
        params={"baseline_run_id": baseline["id"], "candidate_run_id": candidate["id"]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def changes(cmp):
    return {c["case_key"]: c["change"] for c in cmp["case_changes"]}


# ---------------------------------------------------------------- bug 1: scoring config


@pytest.fixture()
def config_mismatch(client):
    ds, _ = make_dataset(client, [{"key": "k", "input": "q", "expected_output": REFERENCE}])
    lenient = run(client, ds["id"], {"k": OUTPUT}, {"min_token_f1": 0.3})
    strict = run(client, ds["id"], {"k": OUTPUT}, {"min_token_f1": 0.6})
    assert lenient["summary"]["passed_cases"] == 1 and strict["summary"]["passed_cases"] == 0
    return lenient, strict


def test_bug1_config_difference_is_not_reported_as_regression(client, config_mismatch):
    cmp = compare(client, *config_mismatch)
    assert cmp["has_regression"] is False
    assert cmp["regression_reasons"] == []
    assert cmp["compatible"] is False
    issue = next(i for i in cmp["compatibility_issues"] if i["kind"] == "scoring_config")
    assert issue["severity"] == "error" and "min_token_f1" in issue["message"]
    assert cmp["case_counts"]["regressed"] == 0


def test_bug1_comparison_gates_fail_closed_without_claiming_regression(client, config_mismatch):
    lenient, strict = config_mismatch
    g = client.post(
        "/evaluations/gates",
        json={
            "candidate_run_id": strict["id"],
            "baseline_run_id": lenient["id"],
            "gates": {"max_regressed_cases": 0, "max_pass_rate_drop": 0},
        },
    ).json()
    assert g["decision"] == "fail"
    assert g["regression_detected"] is None
    for gate in g["gates"]:
        assert gate["status"] == "fail" and "not comparable" in gate["message"]


def test_bug1_report_explains_incompatibility(client, config_mismatch):
    lenient, strict = config_mismatch
    rep = client.get(
        f"/evaluations/runs/{strict['id']}/report", params={"baseline_run_id": lenient["id"]}
    ).json()
    assert rep["comparison"]["compatible"] is False
    assert any("not comparable" in n for n in rep["notes"])
    assert "regressed:" not in rep["markdown"]


def test_identical_config_is_still_comparable(client):
    ds, _ = make_dataset(client, [{"key": "k", "input": "q", "expected_output": REFERENCE}])
    a = run(client, ds["id"], {"k": OUTPUT}, {"min_token_f1": 0.3})
    b = run(client, ds["id"], {"k": OUTPUT}, {"min_token_f1": 0.3})
    cmp = compare(client, a, b)
    assert cmp["compatible"] is True and cmp["has_regression"] is False
    assert cmp["compatibility_issues"] == []


# ---------------------------------------------------------------- bug 2: dataset edits


def test_bug2_edited_case_is_not_reported_as_regression(client):
    ds, cases = make_dataset(
        client,
        [
            {"key": "edited", "input": "q", "expected_output": REFERENCE},
            {"key": "stable", "input": "q2", "required_terms": ["hello"]},
        ],
    )
    responses = {"edited": OUTPUT, "stable": "hello there"}
    before = run(client, ds["id"], responses)
    patch = {"expected_output": "items may be returned within thirty days"}
    assert client.patch(f"/test-cases/{cases['edited']['id']}", json=patch).status_code == 200
    after = run(client, ds["id"], responses)

    cmp = compare(client, before, after)
    assert cmp["has_regression"] is False, cmp["regression_reasons"]
    assert cmp["compatible"] is True
    assert changes(cmp) == {"edited": "definition_changed", "stable": "unchanged_pass"}
    assert cmp["comparable_cases"] == 1
    assert any(i["kind"] == "definition_changed" for i in cmp["compatibility_issues"])
    # Aggregates are computed over comparable cases only: no token F1 exists there.
    f1 = next(d for d in cmp["metric_deltas"] if d["metric"] == "mean_token_f1")
    assert f1["status"] == "not_applicable"


def test_bug2_single_edited_case_leaves_nothing_comparable(client):
    ds, cases = make_dataset(client, [{"key": "k", "input": "q", "expected_output": REFERENCE}])
    before = run(client, ds["id"], {"k": OUTPUT})
    client.patch(f"/test-cases/{cases['k']['id']}", json={"expected_output": "something else"})
    after = run(client, ds["id"], {"k": OUTPUT})
    cmp = compare(client, before, after)
    assert cmp["has_regression"] is False
    assert cmp["compatible"] is False
    assert any(i["kind"] == "no_comparable_cases" for i in cmp["compatibility_issues"])


def test_real_regression_still_detected_next_to_an_edited_case(client):
    ds, cases = make_dataset(
        client,
        [
            {"key": "edited", "input": "q", "expected_output": REFERENCE},
            {"key": "stable", "input": "q2", "required_terms": ["hello"]},
        ],
    )
    before = run(client, ds["id"], {"edited": OUTPUT, "stable": "hello"})
    client.patch(f"/test-cases/{cases['edited']['id']}", json={"expected_output": "changed"})
    after = run(client, ds["id"], {"edited": OUTPUT, "stable": "goodbye"})
    cmp = compare(client, before, after)
    assert cmp["has_regression"] is True
    assert changes(cmp)["stable"] == "regressed"
    assert changes(cmp)["edited"] == "definition_changed"


def test_case_added_between_runs_does_not_dilute_aggregates(client):
    ds, _ = make_dataset(client, [{"key": "a", "input": "q", "required_terms": ["yes"]}])
    before = run(client, ds["id"], {"a": "yes"})
    client.post(
        f"/datasets/{ds['id']}/test-cases",
        json={"key": "b", "input": "q", "required_terms": ["new"]},
    )
    after = run(client, ds["id"], {"a": "yes", "b": "missing term"})
    cmp = compare(client, before, after)
    # Candidate pass rate is 50% overall, but on the shared case it did not regress.
    assert after["summary"]["pass_rate"] == 0.5
    assert cmp["has_regression"] is False
    assert changes(cmp) == {"a": "unchanged_pass", "b": "only_in_candidate"}
    pr = next(d for d in cmp["metric_deltas"] if d["metric"] == "pass_rate")
    assert pr["baseline"] == pr["candidate"] == 1.0


def test_tag_only_edit_keeps_case_comparable(client):
    ds, cases = make_dataset(client, [{"key": "k", "input": "q", "required_terms": ["x"]}])
    before = run(client, ds["id"], {"k": "x"})
    client.patch(f"/test-cases/{cases['k']['id']}", json={"tags": ["new-tag"]})
    after = run(client, ds["id"], {"k": "x"})
    cmp = compare(client, before, after)
    assert cmp["compatible"] is True and changes(cmp) == {"k": "unchanged_pass"}


# ---------------------------------------------------------------- version tracking


def test_versions_are_tracked_on_datasets_cases_runs_and_results(client):
    ds, cases = make_dataset(client, [{"key": "k", "input": "q", "required_terms": ["x"]}])
    case = cases["k"]
    v0 = client.get(f"/datasets/{ds['id']}").json()["version"]
    assert case["version"] == 1 and len(case["content_hash"]) == 64

    r1 = run(client, ds["id"], {"k": "x"})
    assert r1["dataset_version"] == v0
    assert r1["methodology_version"]
    assert r1["results"][0]["case_hash"] == case["content_hash"]
    assert r1["results"][0]["case_version"] == 1
    assert r1["results"][0]["case_snapshot"]["required_terms"] == ["x"]

    tagged = client.patch(f"/test-cases/{case['id']}", json={"tags": ["t"]}).json()
    assert tagged["version"] == 1 and tagged["content_hash"] == case["content_hash"]
    assert client.get(f"/datasets/{ds['id']}").json()["version"] == v0

    edited = client.patch(f"/test-cases/{case['id']}", json={"required_terms": ["y"]}).json()
    assert edited["version"] == 2 and edited["content_hash"] != case["content_hash"]
    assert client.get(f"/datasets/{ds['id']}").json()["version"] == v0 + 1

    client.delete(f"/test-cases/{case['id']}")
    assert client.get(f"/datasets/{ds['id']}").json()["version"] == v0 + 2
    # history is preserved: the old run still carries its snapshot
    old = client.get(f"/evaluations/runs/{r1['id']}").json()
    assert old["results"][0]["case_snapshot"]["required_terms"] == ["x"]
