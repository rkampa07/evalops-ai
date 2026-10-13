import pytest

from conftest import load_example


def runs_of(seeded):
    r = seeded["runs"]
    return r["baseline_prod_v1.3"], r["candidate_v1.4_regressed"], r["candidate_v1.5_fixed"]


def test_create_run_scores_all_cases(seeded):
    baseline, regressed, fixed = runs_of(seeded)
    assert baseline["summary"]["passed_cases"] == 11 and baseline["summary"]["total_cases"] == 12
    assert regressed["summary"]["passed_cases"] == 8
    assert fixed["summary"]["passed_cases"] == 12
    assert len(baseline["results"]) == 12
    assert baseline["dataset_name"] == "kestrel-support-v1"
    assert baseline["config"]["default_max_latency_ms"] == 2500


def test_failures_are_explained(seeded):
    _, regressed, _ = runs_of(seeded)
    by_key = {r["case_key"]: r for r in regressed["results"]}
    comp = by_key["safety-unauthorized-compensation"]
    assert not comp["passed"]
    assert {f["check"] for f in comp["failures"]} == {"required_terms", "forbidden_terms"}
    assert comp["metrics"]["forbidden_terms_found"] == ["guarantee", "500"]
    assert by_key["extract-order-not-delivered"]["metrics"]["json_parsed"] is False


def test_list_and_get_runs(client, seeded):
    listing = client.get("/evaluations/runs").json()
    assert listing["total"] == 3 and len(listing["items"]) == 3
    assert listing["items"][0]["prompt_version"] == "v1.5"  # newest first
    assert "results" not in listing["items"][0]

    filtered = client.get("/evaluations/runs", params={"prompt_version": "v1.3"}).json()
    assert filtered["total"] == 1
    page = client.get("/evaluations/runs", params={"limit": 1, "offset": 1}).json()
    assert len(page["items"]) == 1 and page["total"] == 3

    run_id = listing["items"][0]["id"]
    assert client.get(f"/evaluations/runs/{run_id}").json()["id"] == run_id
    assert client.get("/evaluations/runs/9999").status_code == 404
    assert client.get("/evaluations/runs", params={"limit": 0}).status_code == 422


def test_missing_responses_counted_as_failures(client, seeded):
    ds_id = seeded["dataset"]["id"]
    body = {
        "dataset_id": ds_id,
        "model_name": "m",
        "prompt_version": "p",
        "responses": [
            {"case_key": "latency-greeting", "output": "How can I help?", "latency_ms": 100}
        ],
    }
    run = client.post("/evaluations/runs", json=body).json()
    s = run["summary"]
    assert s["responded_cases"] == 1 and s["passed_cases"] == 1
    assert s["failure_counts"]["missing_response"] == 11


def test_run_validation(client, seeded):
    ds_id = seeded["dataset"]["id"]
    base = {"dataset_id": ds_id, "model_name": "m", "prompt_version": "p"}
    resp = {"case_key": "latency-greeting", "output": "hi"}

    r = client.post("/evaluations/runs", json=base | {"responses": [resp | {"case_key": "nope"}]})
    assert r.status_code == 422 and "unknown case keys" in r.json()["error"]["message"]
    r = client.post("/evaluations/runs", json=base | {"responses": [resp, resp]})
    assert r.status_code == 422 and "duplicate" in r.json()["error"]["message"]
    assert client.post("/evaluations/runs", json=base | {"responses": []}).status_code == 422
    assert (
        client.post(
            "/evaluations/runs", json=base | {"dataset_id": 999, "responses": [resp]}
        ).status_code
        == 404
    )
    bad_cfg = base | {"responses": [resp], "config": {"min_token_f1": 1.5}}
    assert client.post("/evaluations/runs", json=bad_cfg).status_code == 422
    neg = base | {"responses": [resp | {"latency_ms": -5}]}
    assert client.post("/evaluations/runs", json=neg).status_code == 422


def test_run_on_empty_dataset_rejected(client):
    ds = client.post("/datasets", json={"name": "empty"}).json()
    body = {
        "dataset_id": ds["id"],
        "model_name": "m",
        "prompt_version": "p",
        "responses": [{"case_key": "a", "output": "x"}],
    }
    r = client.post("/evaluations/runs", json=body)
    assert r.status_code == 422 and "no test cases" in r.json()["error"]["message"]


def test_compare_detects_regression(client, seeded):
    baseline, regressed, _ = runs_of(seeded)
    r = client.get(
        "/evaluations/compare",
        params={"baseline_run_id": baseline["id"], "candidate_run_id": regressed["id"]},
    )
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["has_regression"] is True
    assert c["case_counts"]["regressed"] == 4 and c["case_counts"]["fixed"] == 1
    changes = {x["case_key"]: x["change"] for x in c["case_changes"]}
    assert changes["policy-phone-hours"] == "fixed"
    assert changes["safety-unauthorized-compensation"] == "regressed"
    statuses = {d["metric"]: d["status"] for d in c["metric_deltas"]}
    assert statuses["pass_rate"] == "regressed"
    assert statuses["schema_pass_rate"] == "regressed"
    assert statuses["latency_p95_ms"] == "improved"


def test_compare_fixed_candidate_has_no_regression(client, seeded):
    baseline, _, fixed = runs_of(seeded)
    c = client.get(
        "/evaluations/compare",
        params={"baseline_run_id": baseline["id"], "candidate_run_id": fixed["id"]},
    ).json()
    assert c["has_regression"] is False and c["case_counts"]["regressed"] == 0


def test_compare_validation(client, seeded):
    baseline, *_ = runs_of(seeded)
    same = {"baseline_run_id": baseline["id"], "candidate_run_id": baseline["id"]}
    assert client.get("/evaluations/compare", params=same).status_code == 422
    assert (
        client.get("/evaluations/compare", params={"baseline_run_id": baseline["id"]}).status_code
        == 422
    )
    missing = {"baseline_run_id": baseline["id"], "candidate_run_id": 999}
    assert client.get("/evaluations/compare", params=missing).status_code == 404

    other = client.post("/datasets", json={"name": "other"}).json()
    client.post(
        f"/datasets/{other['id']}/test-cases",
        json={"key": "k", "input": "i", "required_terms": ["x"]},
    )
    run = client.post(
        "/evaluations/runs",
        json={
            "dataset_id": other["id"],
            "model_name": "m",
            "prompt_version": "p",
            "responses": [{"case_key": "k", "output": "x"}],
        },
    ).json()
    r = client.get(
        "/evaluations/compare",
        params={"baseline_run_id": baseline["id"], "candidate_run_id": run["id"]},
    )
    assert r.status_code == 422 and "same dataset" in r.json()["error"]["message"]


STRICT_GATES = {
    "min_pass_rate": 0.9,
    "max_forbidden_violations": 0,
    "min_schema_pass_rate": 1.0,
    "max_regressed_cases": 0,
}


def test_gates_block_regressed_candidate(client, seeded):
    baseline, regressed, _ = runs_of(seeded)
    r = client.post(
        "/evaluations/gates",
        json={
            "candidate_run_id": regressed["id"],
            "baseline_run_id": baseline["id"],
            "gates": STRICT_GATES,
        },
    )
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["decision"] == "fail" and g["regression_detected"] is True
    statuses = {x["gate"]: x["status"] for x in g["gates"]}
    assert statuses == {
        "min_pass_rate": "fail",
        "max_forbidden_violations": "fail",
        "min_schema_pass_rate": "fail",
        "max_regressed_cases": "fail",
    }


def test_gates_pass_fixed_candidate(client, seeded):
    baseline, _, fixed = runs_of(seeded)
    g = client.post(
        "/evaluations/gates",
        json={
            "candidate_run_id": fixed["id"],
            "baseline_run_id": baseline["id"],
            "gates": STRICT_GATES | {"max_p95_latency_ms": 1000},
        },
    ).json()
    assert g["decision"] == "pass" and g["failed_gates"] == 0 and g["regression_detected"] is False


def test_gates_validation(client, seeded):
    _, regressed, _ = runs_of(seeded)
    assert (
        client.post(
            "/evaluations/gates", json={"candidate_run_id": regressed["id"], "gates": {}}
        ).status_code
        == 422
    )
    bad = {"candidate_run_id": regressed["id"], "gates": {"min_pass_rate": 2}}
    assert client.post("/evaluations/gates", json=bad).status_code == 422
    same = {
        "candidate_run_id": regressed["id"],
        "baseline_run_id": regressed["id"],
        "gates": {"min_pass_rate": 0.5},
    }
    assert client.post("/evaluations/gates", json=same).status_code == 422
    # comparison gate without baseline fails closed
    g = client.post(
        "/evaluations/gates",
        json={"candidate_run_id": regressed["id"], "gates": {"max_regressed_cases": 0}},
    ).json()
    assert g["decision"] == "fail"


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("candidate_v1.4_regressed", "not_ready"),
        ("candidate_v1.5_fixed", "ready"),
    ],
)
def test_report_readiness(client, seeded, candidate, expected):
    baseline = seeded["runs"]["baseline_prod_v1.3"]
    run = seeded["runs"][candidate]
    r = client.get(
        f"/evaluations/runs/{run['id']}/report", params={"baseline_run_id": baseline["id"]}
    )
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["readiness"] == expected
    assert rep["comparison"]["baseline"]["id"] == baseline["id"]
    assert "max_regressed_cases" in rep["gate_policy"]
    assert any("factual correctness" in n for n in rep["notes"])
    assert rep["markdown"].startswith("# Release readiness")


def test_report_without_baseline_omits_comparison_gates(client, seeded):
    _, _, fixed = runs_of(seeded)
    rep = client.get(f"/evaluations/runs/{fixed['id']}/report").json()
    assert rep["comparison"] is None and rep["baseline"] is None
    assert "max_regressed_cases" not in rep["gate_policy"]
    assert rep["readiness"] == "ready"
    assert any("No baseline" in n for n in rep["notes"])


def test_report_markdown_format(client, seeded):
    baseline, regressed, _ = runs_of(seeded)
    r = client.get(
        f"/evaluations/runs/{regressed['id']}/report",
        params={"baseline_run_id": baseline["id"], "format": "markdown"},
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert "NOT READY FOR RELEASE" in r.text
    assert "| safety-unauthorized-compensation |" in r.text


def test_delete_run_and_dataset_protection(client, seeded):
    ds_id = seeded["dataset"]["id"]
    r = client.delete(f"/datasets/{ds_id}")
    assert r.status_code == 409
    for run in seeded["runs"].values():
        assert client.delete(f"/evaluations/runs/{run['id']}").status_code == 204
    assert client.delete(f"/datasets/{ds_id}").status_code == 204


def test_results_snapshot_survives_case_edit(client, seeded):
    baseline, *_ = runs_of(seeded)
    case = client.get(f"/datasets/{seeded['dataset']['id']}/test-cases").json()[0]
    client.delete(f"/test-cases/{case['id']}")
    run = client.get(f"/evaluations/runs/{baseline['id']}").json()
    assert len(run["results"]) == 12
    assert any(r["test_case_id"] is None for r in run["results"])


def test_fixture_files_are_valid_examples():
    assert len(load_example("datasets/kestrel_support_v1.json")["test_cases"]) == 12
